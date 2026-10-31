"""Generic Portuguese receipt parser.

Used for merchants without a calibrated parser, and as the base of the ones calibrated on a single
layout. It recognises the common shapes of an invoice line and stops at the total:

    DESCRIPTION  3,49 €  3        <- description, amount (optional €), VAT class (letter or digit)
    1 DESCRIPTION  2,90  D        <- count first (Mercadona)
    3 DESCRIPTION  1,25  3,75  B  <- count, unit price, total
    DESCRIPTION  2 X 1,65  3,30   <- count and unit price inside the line
    0,298 kg x 1,99 €/kg          <- quantity line, printed before or after the line it belongs to
    DESCONTO 0,46-€               <- discount on the previous line
    (A) DESCRIPTION               <- description alone, its count and prices on the next line:
    3 X 2,69 8,07                    quantity, unit price and line total
    POUPANCA 4,00                 <- a saving, with no sign: on the previous line, or on the next one when
                                     the receipt prints its savings first
    Frutaria:                     <- a section title, skipped

A searchable PDF with a column header ("Produtos  Preço unitário  Tipo imposto  Quantidade  TOTAL") is read
by columns (`table.py`). The total is chosen among the printed ones by the lines and the payments
(`totals.py`).

A quantity line belongs to the neighbouring line whose total it explains (count × unit price), so the
layout does not have to be known: Lidl prints it after the item, Aldi before it.

Every field is capped below the review threshold, so its output is never auto-confirmed. Calibrated
subclasses raise the cap and override the markers.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents
from basketiq.domain.normalization import normalize_key
from basketiq.domain.status import Merchant, ProcessingRoute, Unit
from basketiq.pipeline.parsers.base import (
    COUNT_INFERRED_CONFIDENCE,
    MONEY,
    RULE_EXACT,
    build_item,
    choose_purchase_datetime,
    count_from_total,
    flag_ambiguous_unit,
    guess_merchant_name,
    is_weight_quantity,
    one_letter_apart,
    parse_date_candidates,
    terminal_store_label,
    unresolved_line,
)
from basketiq.pipeline.parsers.table import first_column_role, is_header_row, table_items
from basketiq.pipeline.parsers.totals import choose_total, line_sums, settle_savings
from basketiq.pipeline.schema import AMOUNT_TOKEN, DocumentText, ParsedLine, ParsedReceipt, TextLine

CONFIDENCE_CAP = 0.6
_MIN_CODE_ROWS = 2  # rows that must start with a VAT code of the same width before it is cut
CALIBRATED_CAP = 0.9  # merchants read from a few real samples: reviewed when the OCR is unsure
PAIRED_CONFIDENCE = 0.7  # a price joined to a description that was on another row: always reviewed

_EURO = r"(?:\s*(?:€|EUR|[eEcC](?=\s|$)))?"  # a lone "e" or "c" is the euro sign as the OCR read it
# The VAT class, and a stray character the OCR may add after it. A photo of a small receipt misreads the class
# too: "€ 1" as "ei" or "el", "B" as "0", "o", ".0" or ")0", and "€ 3" as "---".
_TAX = r"(?:\s*[A-Za-z0-9).]){0,2}(?:\s*[-–]{1,3})?"
_ITEM = re.compile(
    rf"^(?:\(?[A-Z]\)?\s+)?(?:(?P<lead>\d{{1,2}})\s+)?(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+"
    rf"(?:(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?P<qunit>kg|un)?\s*[xX×]\s*(?P<unit>{MONEY}){_EURO}\s+"
    rf"|(?P<unit2>{MONEY}){_EURO}\s+(?=-?{MONEY}))?"
    rf"(?P<total>-?{MONEY})(?!\d){_EURO}{_TAX}$"  # "810.285" is no amount followed by a class "5"
)
# "0,298 kg x 1,99 €/kg", "6 x 0,99 €", "2x0,10€" (no description); on a tilted photo the VAT class of the
# next row may land at its end ("6 x 0,99 € 1")
_QTY_LINE = re.compile(
    rf"^(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?P<kg>kg)?\s*[xX×]\s*(?P<unit>{MONEY}){_EURO}(?:\s*/\s*kg)?"
    rf"(?:\s+[0-9A-Z])?$",
    re.I,
)
# "1,004 kg 2,50 €/kg 2,51 B", "0,298 kg x 1,99 €/kg 0,59 € 1": quantity, unit price and total, under a
# description-only line or after the description when the OCR joined them ("CURGETE 0,444 kg x ...")
_WEIGHED_LINE = re.compile(
    rf"^(?:(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+)?(?P<qty>\d+[,.]\d{{1,3}})\s*k[g90]\s*[xX×]?\s*(?P<unit>{MONEY})"
    rf"(?P<cur>\s*(?:€|EUR|[eE](?=\s*/)))?"  # the OCR may read the euro sign of "€/kg" as a lone "e"
    rf"\s*/\s*k[g90]\s+(?P<total>{MONEY}){_EURO}{_TAX}$",
    re.I,
)
_DISCOUNT = re.compile(
    rf"^(?:Desconto|Poupan[çc]a|Promo[çc][ãa]o|Cup[ãa]o|Vale)\b.*?"
    rf"(?:[(-]\s*(?P<a1>{MONEY})\s*\)?|(?P<a2>{MONEY})\s*-)\s*(?:€|EUR)?$",
    re.I,
)
_TOTAL = re.compile(
    rf"^(?:TOTAL(?:\s+A\s+PAGAR|\s+COMPRA|\s+PAGO)?(?:\s+[A-Z])?|A\s+PAGAR)\s*:?\s+(?P<amount>{MONEY}){_EURO}$",
    re.I,
)
_STOP = re.compile(
    r"^(SUBTOTAL|TOTAL|A\s+PAGAR|IVA\b|TAXA|MULTIBANCO|NUMER[AÁ]RIO|TROCO|PAGAMENTO|Valor\s+dep[oó]sito)",
    re.I,
)
# "3 X 2,69 8,07": quantity, unit price and line total, under the line that names the product
_QTY_TOTAL = re.compile(
    rf"^(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*[xX×]\s*(?P<unit>{MONEY}){_EURO}\s+(?P<total>{MONEY}){_EURO}{_TAX}$"
)
# "X 2,68 5,36": the OCR dropped the count in front of "X"; unit price and line total are left
_LOST_COUNT = re.compile(rf"^[xX×]\s*(?P<unit>{MONEY}){_EURO}\s+(?P<total>{MONEY}){_EURO}{_TAX}$")
_SAVING_PLAIN = re.compile(rf"^(?:Poupan[çc]a|Desconto)\s+(?P<amount>{MONEY}){_EURO}$", re.I)
_CATEGORY = re.compile(r"^[A-Za-zÀ-ÿ&/+.' -]+:$")  # "Charcutaria&Queijos:"
_TAGGED = re.compile(r"^\((?P<tax>[A-Z0-9])\)\s+(?P<desc>.+)$")  # "(A) QUEIJO FRESCO"
# "BATATA BRANCA 2,5KG 229 E": a price whose comma the OCR lost (see `_recover_lost_comma`)
_LOST_COMMA = re.compile(rf"^(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+(?P<cents>\d{{3,4}}){_EURO}{_TAX}$")
_PRICE_TAIL = rf"(?P<total>{MONEY})(?!\d){_EURO}{_TAX}$"
_AMOUNT_AT_END = re.compile(_PRICE_TAIL)  # .match: a price alone on its row; .search: a price at its end
_COLUMN_TITLE = re.compile(r"PRE[CÇ]O|IVA|VALOR|TOTAL", re.I)
_HEADER_NOISE = re.compile(r"^(?:DENOMINA[CÇ][AÃ]O|DESCRI[CÇ][AÃ]O)\s+|\s+PRE[CÇ]O\s+IVA$", re.I)


class GenericPtParser:
    version = "generic_pt-1.0"
    cap = CONFIDENCE_CAP
    stop = _STOP
    total = _TOTAL
    geometry = True  # read a searchable PDF's column table by x position, when it has a header
    _from_ocr = False  # set by `parse` for the receipt it reads (a parser instance reads one receipt)
    # set by `parse` too: rows that may be an item whose price lost its comma (position, row, match)
    _lost_comma: list[tuple[int, TextLine, re.Match[str]]]

    def __init__(self, merchant: Merchant) -> None:
        self.merchant = merchant

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        self._from_ocr = doc.route == ProcessingRoute.LOCAL_OCR
        self._lost_comma = []
        end = next(
            (
                i
                for i, line in enumerate(lines)
                if self.stop.match(line.text.strip()) and not is_header_row(line.text.strip())
            ),
            len(lines),
        )
        parsed = table_items(lines, end) if self.geometry and doc.route == ProcessingRoute.PDF_TEXT else None
        if parsed is None:
            zone = lines[self._start(lines[:end]) : end]
            parsed = self._items(_without_vat_codes(zone) if _vat_code_first(lines[:end]) else zone)

        for item in parsed:
            item.field_confidence = {k: round(min(v, self.cap), 3) for k, v in item.field_confidence.items()}

        total = choose_total(lines[end:], parsed, self.total)
        self._recover_lost_comma(parsed, total)
        settle_savings(parsed, total)
        purchase_at, date_conf = choose_purchase_datetime(parse_date_candidates(lines))
        return ParsedReceipt(
            merchant=self.merchant,
            purchase_at=purchase_at,
            total_cents=total,
            lines=parsed,
            store_label=self._store(lines),
            merchant_name=guess_merchant_name(lines) if self.merchant == Merchant.UNKNOWN else None,
            field_confidence={
                "purchase_at": min(date_conf, self.cap),
                "total": self.cap if total is not None else 0.0,
            },
            parser_version=self.version,
            notes=["generic_parser_uncalibrated"] if self.cap == CONFIDENCE_CAP else [],
        )

    def _start(self, head: list[TextLine]) -> int:
        """Index of the first item line in the part of the receipt before the total."""
        for i, line in enumerate(head):
            if _ITEM.match(_text(line)) or _saving(_text(line)) is not None:
                return i  # a saving before the first item: the receipt prints its savings first
            if i + 1 < len(head) and _is_qty_total(_text(head[i + 1])) and _names_a_product(_text(line)):
                return i  # the description alone, with its quantity and prices on the next line
        return len(head)

    def _store(self, lines: list[TextLine]) -> str | None:
        return terminal_store_label(lines)

    def _recover_lost_comma(self, parsed: list[ParsedLine], total: int | None) -> None:
        """A row read as a description alone because the OCR lost the comma of its price ("2,5KG 229 E") is
        the missing item when its number is exactly what the lines lack to reach the printed total, and no
        other such row would do. A bare number can be part of a name ("500 G"), so the arithmetic decides;
        the item is reviewed."""
        if total is None or not self._lost_comma:
            return
        gap = total - min(line_sums(parsed), default=0)  # the smaller sum is the one net of the discounts
        fits = [(at, row, m) for at, row, m in self._lost_comma if int(m.group("cents")) == gap]
        if len(fits) != 1:
            return
        at, row, m = fits[0]
        item = build_item(
            description=m.group("desc"),
            total_text=f"{gap // 100},{gap % 100:02d}",
            line_conf=min(row.confidence, COUNT_INFERRED_CONFIDENCE),
            evidence=[_text(row)],
        )
        parsed.insert(at, item)

    def _items(self, zone: list[TextLine]) -> list[ParsedLine]:
        parsed: list[ParsedLine] = []
        pending_qty: re.Match[str] | None = None  # quantity line waiting for the item it explains
        pending_text = ""
        pending_description: str | None = None  # description waiting for its quantity and prices line
        pending_tax: str | None = None  # the "(A)" printed before that description
        pending_amount: TextLine | None = None  # a price alone on its row, waiting for its description
        pending_saving: tuple[int, float] | None = None  # a saving printed before its item (see below)
        pending_weighed: tuple[re.Match[str], TextLine] | None = None  # a weighed line whose name comes next
        savings_first = _savings_first(zone)

        def add(item: ParsedLine) -> None:
            nonlocal pending_saving
            if pending_saving is not None and not item.unresolved:
                _apply_saving(item, *pending_saving, cap=self.cap)
                pending_saving = None
            parsed.append(item)

        def flush_amount() -> None:
            nonlocal pending_amount
            if pending_amount is not None:
                parsed.append(unresolved_line(pending_amount))
                pending_amount = None

        def flush_weighed() -> None:  # no name came for it: left for review
            nonlocal pending_weighed
            if pending_weighed is not None:
                parsed.append(unresolved_line(pending_weighed[1]))
                pending_weighed = None

        for n, line in enumerate(zone):
            text = _text(line)
            if pending_weighed is not None:
                nxt = _text(zone[n + 1]) if n + 1 < len(zone) else ""
                own_prices = _is_qty_total(nxt) or bool(_QTY_LINE.match(nxt))  # the name of another item
                if _names_a_product(text) and not own_prices:
                    wm, wline = pending_weighed
                    pending_weighed = None
                    add(_weighed_item(wm, text, min(wline.confidence, line.confidence), [_text(wline), text]))
                    continue
                flush_weighed()
            if _CATEGORY.match(text):  # a section title: it names no product
                flush_amount()
                pending_description = pending_tax = None
                continue
            if (saving := _saving(text)) is not None:
                if savings_first:
                    pending_saving = (saving, line.confidence)
                elif parsed and not parsed[-1].unresolved:
                    _apply_saving(parsed[-1], saving, line.confidence, cap=self.cap)
                    parsed[-1].evidence.append(text)
                continue
            if m := _WEIGHED_LINE.match(text):
                description = m.group("desc") or pending_description
                if (m.group("cur") or "").strip() in ("e", "E") and not _weight_closes(m):
                    parsed.append(
                        unresolved_line(line)
                    )  # "e/kg" is a euro sign only if the arithmetic agrees
                elif description:
                    add(_weighed_item(m, description, line.confidence, [description, text]))
                elif _weight_closes(m):
                    pending_weighed = (m, line)  # the name is the next row; without it, it is left for review
                else:
                    parsed.append(unresolved_line(line))
                pending_description = pending_tax = None
                continue
            if (m := _QTY_TOTAL.match(text)) and pending_description:
                add(
                    build_item(
                        description=pending_description,
                        total_text=m.group("total"),
                        qty_text=m.group("qty"),
                        unit_price_text=m.group("unit"),
                        tax_code=pending_tax,
                        line_conf=line.confidence,
                        evidence=[pending_description, text],
                    )
                )
                pending_description = pending_tax = None
                continue
            if (m := _QTY_TOTAL.match(text)) and _weight_closes(m):
                # "4 x 0,14 € 0,56 € 1" with no name before it: a count printed before its item (Aldi), whose
                # total a tilt lifted onto the count's row. Like a weighed line, it waits for the next name.
                flush_weighed()
                pending_weighed = (m, line)
                continue
            if (m := _LOST_COUNT.match(text)) and pending_description:
                add(_item_with_lost_count(pending_description, m, pending_tax, line, text))
                pending_description = pending_tax = None
                continue
            if m := _QTY_LINE.match(text):
                if parsed and not parsed[-1].unresolved and _explains(m, parsed[-1]):
                    _apply_quantity(parsed[-1], m, line.confidence, text)
                else:
                    pending_qty = m
                    pending_text = text
                pending_description = pending_tax = None
                continue
            if m := _ITEM.match(text):
                flush_amount()
                if _COLUMN_TITLE.fullmatch(_description(m)):
                    # "DENOMINAÇÃO PREÇO 2,99 € 1": a tilt lifted the first item's price onto the column
                    # titles. Like a price alone, it waits for the name on the next row.
                    pending_amount = line
                    pending_qty = pending_description = pending_tax = None
                    continue
                item = _item(m, line, text, self._from_ocr)
                if pending_qty is not None and _explains(pending_qty, item):
                    _apply_quantity(item, pending_qty, line.confidence, pending_text)
                pending_qty = pending_description = pending_tax = None
                add(item)
            elif _AMOUNT_AT_END.match(text):  # a price alone: the OCR put it on a row of its own
                flush_amount()
                if pending_description:
                    add(_orphan_item(pending_description, line))
                    pending_description = pending_tax = None
                else:
                    pending_amount = line
                pending_qty = None
            elif re.search(MONEY, text):
                flush_amount()
                parsed.append(unresolved_line(line))
                pending_qty = pending_description = pending_tax = None
            elif re.search(r"[A-Za-zÀ-ÿ]{3}", text):  # a description alone
                if self._from_ocr and (lost := _LOST_COMMA.match(text)):
                    self._lost_comma.append((len(parsed), line, lost))
                tagged = _TAGGED.match(text)
                description = re.sub(
                    r"^\d{1,2}\s+", "", tagged.group("desc") if tagged else text
                )  # "1 COUVE-FLOR"
                if pending_amount is not None:
                    add(_orphan_item(description, pending_amount))
                    pending_amount = pending_description = pending_tax = None
                else:
                    pending_description = description
                    pending_tax = tagged.group("tax") if tagged else None
        flush_amount()
        flush_weighed()
        return parsed


def _weighed_item(m: re.Match[str], description: str, line_conf: float, evidence: list[str]) -> ParsedLine:
    return build_item(
        description=description,
        total_text=m.group("total"),
        qty_text=m.group("qty"),
        unit_price_text=m.group("unit"),
        weight_unit=Unit.KG,
        unit_printed=True,  # the weighed line says kg
        line_conf=line_conf,
        evidence=evidence,
    )


def _item_with_lost_count(
    description: str, m: re.Match[str], tax: str | None, line: TextLine, text: str
) -> ParsedLine:
    """The count is the line total divided by the unit price, when that comes to two or more (see
    `count_from_total`). It was not printed, so the line is reviewed (the unit and the prices are read, the
    count is not)."""
    count = count_from_total(parse_money_cents(m.group("total")), parse_money_cents(m.group("unit")))
    if count is None or count < 2:
        # Not a whole count (a weight, a price misread beyond one digit): the name and the total are kept.
        left_over = unresolved_line(line)
        left_over.raw_description = description
        left_over.evidence = [description, text]
        return left_over
    item = build_item(
        description=description,
        total_text=m.group("total"),
        qty_text=str(count),
        unit_price_text=m.group("unit"),
        tax_code=tax,
        line_conf=line.confidence,
        evidence=[description, text],
    )
    item.field_confidence["quantity"] = min(item.field_confidence["quantity"], COUNT_INFERRED_CONFIDENCE)
    return item


_VAT_CODE = re.compile(r"^(?P<code>\d{1,2}|[A-Z]\d|\d[A-Z])\s+(?=\S)")  # "12 NAME", "I2 NAME" (a misread 1)


def _vat_code_first(head: list[TextLine]) -> bool:
    """The column titles say the VAT code comes first ("IVA ARTIGO/QT/LI PRECO VALOR")."""
    return any(first_column_role(_text(line)) == "tax" for line in head)


def _without_vat_codes(zone: list[TextLine]) -> list[TextLine]:
    """The rows of a receipt whose first column is the VAT code, without that code.

    Only a leading token with the shape of a code is cut, and only when at least two rows start with one
    of the same length: the column has a fixed width, so a name that begins with a number of another length
    ("3 CHEFS" among two-character codes) or with a short word ("7UP") is not a code and stays."""
    matches = [(line, _VAT_CODE.match(_text(line))) for line in zone]
    widths = Counter(len(m.group("code")) for _, m in matches if m)
    if not widths:
        return zone
    width, rows = widths.most_common(1)[0]
    if rows < _MIN_CODE_ROWS:
        return zone
    return [
        _cut_prefix(line, m.end()) if m and len(m.group("code")) == width else line for line, m in matches
    ]


def _cut_prefix(line: TextLine, size: int) -> TextLine:
    """The line without its first `size` characters (the tokens that were cut away completely are dropped)."""
    tokens, dropped = [], 0
    for token in line.tokens:
        text = token.text
        skip = max(0, min(len(text), size - dropped))
        dropped += len(text) + 1
        rest = text[skip:].lstrip()
        if rest:
            tokens.append(token.model_copy(update={"text": rest}))
    return line.model_copy(update={"tokens": tokens})


_DISCOUNT_WORDS = {"DESCONTO": "Desconto", "POUPANCA": "Poupança", "PROMOCAO": "Promoção", "CUPAO": "Cupão"}
_MIN_TOLERATED = 6  # letters: a shorter word ("Vale") is too close to others to forgive a misread letter
_NEGATIVE_AMOUNT = re.compile(rf"(?:[(-]\s*{MONEY}\s*\)?|{MONEY}\s*-)\s*(?:€|EUR)?$")


def _with_discount_word(text: str) -> str:
    """ "CESCONTO 0.22-€": the discount word with one letter misread, at the start of a line that ends in a
    negative amount, is the word it was. Without the negative amount nothing changes."""
    word, _, rest = text.partition(" ")
    if not _NEGATIVE_AMOUNT.search(rest):  # the cheap test first: nearly every line is not a discount
        return text
    key = normalize_key(word)
    if len(key) < _MIN_TOLERATED:
        return text
    known = next((w for w in _DISCOUNT_WORDS if one_letter_apart(key, w)), None)
    return f"{_DISCOUNT_WORDS[known]} {rest}" if known else text


def _saving(text: str) -> int | None:
    """The amount of a saving line ("Poupança Imediata (0,60)", "DESCONTO 0,46-€", "POUPANCA 4,00")."""
    text = _with_discount_word(text)
    if m := _DISCOUNT.match(text):
        return abs(parse_money_cents(m.group("a1") or m.group("a2")) or 0)
    if m := _SAVING_PLAIN.match(text):
        return parse_money_cents(m.group("amount"))
    return None


def _savings_first(zone: list[TextLine]) -> bool:
    """A receipt that prints each saving before its item has a saving as the first line with an amount."""
    first = next((t for line in zone if re.search(MONEY, t := _text(line))), "")
    return _saving(first) is not None


def _apply_saving(item: ParsedLine, cents: int, confidence: float, *, cap: float) -> None:
    item.discount_cents += cents
    item.field_confidence["discount"] = cap * confidence


def _weight_closes(m: re.Match[str]) -> bool:
    """Weight times price per kg gives the total of a weighed line."""
    qty, unit, total = (
        parse_decimal(m.group("qty")),
        parse_money_cents(m.group("unit")),
        parse_money_cents(m.group("total")),
    )
    return (
        qty is not None
        and unit is not None
        and total is not None
        and abs(multiply_cents(qty, unit) - total) <= 1
    )


def _is_qty_total(text: str) -> bool:
    """The row under a description that gives its prices: "3 X 2,69 8,07", or "X 2,68 5,36" (count lost)."""
    return bool(_QTY_TOTAL.match(text) or _LOST_COUNT.match(text))


def _names_a_product(text: str) -> bool:
    """A line that could be the name of a product: letters, no amount, not a title or a column header."""
    return (
        bool(re.search(r"[A-Za-zÀ-ÿ]{3}", text))
        and not re.search(MONEY, text)
        and not _CATEGORY.match(text)
        and not is_header_row(text)
    )


def _text(line: TextLine) -> str:
    return line.text.strip()


def _orphan_item(description: str, amount: TextLine) -> ParsedLine:
    """A description and a price that the OCR read on two different rows: they belong together (the price
    sits just above or below its name), but the pairing is a guess, so the line is reviewed."""
    m = _AMOUNT_AT_END.search(_text(amount))
    assert m is not None
    item = build_item(
        description=description,
        total_text=m.group("total"),
        line_conf=amount.confidence,
        evidence=[description, _text(amount)],
    )
    item.field_confidence = {k: min(v, PAIRED_CONFIDENCE) for k, v in item.field_confidence.items()}
    return item


def _description(m: re.Match[str]) -> str:
    """The name of an `_ITEM` match, without column titles glued to it or a stray "-" (a misread digit)."""
    return re.sub(r"^[-–•*.]+\s*", "", _HEADER_NOISE.sub("", m.group("desc")).strip())


def _item(m: re.Match[str], line: TextLine, text: str, from_ocr: bool = False) -> ParsedLine:
    desc = _description(m)
    if re.search(rf"{MONEY}(?![\dA-Za-z])", desc):
        # An amount inside the name ("PETINGA TOMATE P2 1,70 8 2,00 B"): the OCR joined this row with the
        # next one, whose price is the last amount. Taking it would be a silent guess: leave it to review.
        return unresolved_line(line)
    unit, qty, lead = m.group("unit") or m.group("unit2"), m.group("qty"), m.group("lead")
    if lead:
        total, price = parse_money_cents(m.group("total")), parse_money_cents(unit or "")
        if qty is None and total is not None and price is not None and _times(lead, price) == total:
            qty = lead  # "3 TOALHITA 1,25 3,75": the leading count is the quantity
        elif lead != "1" or unit:
            desc = f"{lead} {desc}"  # "24 OVOS FRESCOS": the number is part of the name
        # a leading 1 with no unit price is just the count: dropped from the name
    worked_out = False
    if qty is None and unit:
        total, price = parse_money_cents(m.group("total")), parse_money_cents(unit)
        if total and price and total % price == 0 and total // price > 1:
            # "BIJO 0,12 0,84": the count was lost, 7 × 0,12. A unit price that divides the total exactly
            # is a checksum (a misread digit would not divide), so it is as reliable as the amounts.
            qty = str(total // price)
            worked_out = True
    if qty is None and unit:
        # Two amounts and no count that explains them: the OCR joined this row with the next one (the
        # price of another product). Taking the last amount would be a silent guess: leave it to review.
        return unresolved_line(line)
    item = build_item(
        description=desc,
        total_text=m.group("total"),
        qty_text=qty,
        unit_price_text=unit if qty else None,
        unit_printed=bool(m.group("qunit")),
        line_conf=line.confidence,
        evidence=[text],
    )
    _rescore(item, line, lead_is_quantity=bool(lead) and qty == lead)
    if worked_out and from_ocr:  # in a PDF the text is exact, in a photo a misread digit can still divide
        item.field_confidence["quantity"] = min(item.field_confidence["quantity"], COUNT_INFERRED_CONFIDENCE)
    return item


_COUNT_TOKEN = re.compile(r"\d{1,2}")


def _rescore(item: ParsedLine, line: TextLine, lead_is_quantity: bool) -> None:
    """Score each field by the tokens that carry it instead of the weakest token of the whole line.

    A leading "1" read with 0.8 says nothing about the price, and where the count is not printed with a
    unit price it can only be 1 (two or more come with a unit price), so it only counts for the quantity
    when it is the quantity."""
    base = line.confidence
    if base <= 0:
        return
    amounts: list[float] = []
    counts: list[float] = []
    words: list[float] = []
    for t in line.value_tokens:
        text = t.text.strip()
        (amounts if AMOUNT_TOKEN.match(text) else counts if _COUNT_TOKEN.fullmatch(text) else words).append(
            t.confidence
        )
    money = min(amounts, default=base)
    scores = {
        "description": min(words, default=base),
        "line_total": money,
        "unit_price": money,
        "quantity": min([money, *counts]) if lead_is_quantity else money,
    }
    for name, score in scores.items():
        if name in item.field_confidence:
            item.field_confidence[name] = round(min(item.field_confidence[name] * score / base, 1.0), 3)


def _times(qty: str, price_cents: int) -> int:
    return multiply_cents(Decimal(qty), price_cents)


def _explains(qty_line: re.Match[str], item: ParsedLine) -> bool:
    """Does count × unit price give the total of this line?"""
    qty, unit = parse_decimal(qty_line.group("qty")), parse_money_cents(qty_line.group("unit"))
    if qty is None or unit is None or item.line_total_cents is None:
        return False
    return abs(multiply_cents(qty, unit) - (item.line_total_cents + item.discount_cents)) <= 1


def _apply_quantity(item: ParsedLine, qty_line: re.Match[str], conf: float, evidence: str) -> None:
    qty = parse_decimal(qty_line.group("qty"))
    if qty is None:
        return
    item.quantity = qty
    item.unit = (
        Unit.KG if is_weight_quantity(qty_line.group("qty"), bool(qty_line.group("kg"))) else Unit.UNIT
    )
    item.unit_price_cents = parse_money_cents(qty_line.group("unit"))
    item.field_confidence["quantity"] = item.field_confidence["unit_price"] = round(RULE_EXACT * conf, 3)
    item.evidence.append(evidence)
    flag_ambiguous_unit(item, qty_line.group("qty"), bool(qty_line.group("kg")))
