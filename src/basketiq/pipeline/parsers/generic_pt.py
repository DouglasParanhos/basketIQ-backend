"""Generic Portuguese receipt parser.

Used for merchants without a calibrated parser, and as the base of the ones calibrated on a single
layout. It recognises the common shapes of an invoice line and stops at the total:

    DESCRIPTION  3,49 €  3        <- description, amount (optional €), VAT class (letter or digit)
    1 DESCRIPTION  2,90  D        <- count first (Mercadona)
    3 DESCRIPTION  1,25  3,75  B  <- count, unit price, total
    DESCRIPTION  2 X 1,65  3,30   <- count and unit price inside the line
    0,298 kg x 1,99 €/kg          <- quantity line, printed before or after the line it belongs to
    DESCONTO 0,46-€               <- discount on the previous line

A quantity line belongs to the neighbouring line whose total it explains (count × unit price), so the
layout does not have to be known: Lidl prints it after the item, Aldi before it.

Every field is capped below the review threshold, so its output is never auto-confirmed. Calibrated
subclasses raise the cap and override the markers.
"""

from __future__ import annotations

import re
from decimal import Decimal

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents
from basketiq.domain.status import Merchant, Unit
from basketiq.pipeline.parsers.base import (
    MONEY,
    RULE_EXACT,
    build_item,
    choose_purchase_datetime,
    find_amount,
    guess_merchant_name,
    is_weight_quantity,
    parse_date_candidates,
    terminal_store_label,
    unresolved_line,
)
from basketiq.pipeline.schema import AMOUNT_TOKEN, DocumentText, ParsedLine, ParsedReceipt, TextLine

CONFIDENCE_CAP = 0.6
CALIBRATED_CAP = 0.9  # merchants read from a few real samples: reviewed when the OCR is unsure
PAIRED_CONFIDENCE = 0.7  # a price joined to a description that was on another row: always reviewed

_EURO = r"(?:\s*(?:€|EUR))?"
_TAX = r"(?:\s*[A-Z0-9]){0,2}"  # the VAT class, and a stray character the OCR may add after it
_ITEM = re.compile(
    rf"^(?:\(?[A-Z]\)?\s+)?(?:(?P<lead>\d{{1,2}})\s+)?(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+"
    rf"(?:(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?:kg|un)?\s*[xX×]\s*(?P<unit>{MONEY}){_EURO}\s+"
    rf"|(?P<unit2>{MONEY}){_EURO}\s+(?=-?{MONEY}))?"
    rf"(?P<total>-?{MONEY}){_EURO}{_TAX}$"
)
# "0,298 kg x 1,99 €/kg", "6 x 0,99 €", "2x0,10€" (no description)
_QTY_LINE = re.compile(
    rf"^(?P<qty>\d+(?:[,.]\d{{1,3}})?)\s*(?P<kg>kg)?\s*[xX×]\s*(?P<unit>{MONEY}){_EURO}(?:\s*/\s*kg)?$", re.I
)
# "1,004 kg 2,50 €/kg 2,51 B", "0,298 kg x 1,99 €/kg 0,59 € 1": quantity, unit price and total, under a
# description-only line or after the description when the OCR joined them ("CURGETE 0,444 kg x ...")
_WEIGHED_LINE = re.compile(
    rf"^(?:(?P<desc>.*?[A-Za-zÀ-ÿ]{{2}}.*?)\s+)?(?P<qty>\d+[,.]\d{{1,3}})\s*k[g90]\s*[xX×]?\s*(?P<unit>{MONEY}){_EURO}"
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
_AMOUNT_ONLY = re.compile(rf"^(?P<total>{MONEY}){_EURO}{_TAX}$")
_HEADER_NOISE = re.compile(r"^(?:DENOMINA[CÇ][AÃ]O|DESCRI[CÇ][AÃ]O)\s+|\s+PRE[CÇ]O\s+IVA$", re.I)


class GenericPtParser:
    version = "generic_pt-1.0"
    cap = CONFIDENCE_CAP
    stop = _STOP
    total = _TOTAL

    def __init__(self, merchant: Merchant) -> None:
        self.merchant = merchant

    def parse(self, doc: DocumentText) -> ParsedReceipt:
        lines = doc.lines
        end = next((i for i, line in enumerate(lines) if self.stop.match(line.text.strip())), len(lines))
        parsed = self._items(lines[self._start(lines[:end]) : end])

        for item in parsed:
            item.field_confidence = {k: round(min(v, self.cap), 3) for k, v in item.field_confidence.items()}

        total, _, _ = find_amount(lines[end:], self.total)
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
        return next((i for i, line in enumerate(head) if _ITEM.match(_text(line))), len(head))

    def _store(self, lines: list[TextLine]) -> str | None:
        return terminal_store_label(lines)

    def _items(self, zone: list[TextLine]) -> list[ParsedLine]:
        parsed: list[ParsedLine] = []
        pending_qty: re.Match[str] | None = None  # quantity line waiting for the item it explains
        pending_text = ""
        pending_description: str | None = None  # description waiting for a "1,004 kg 2,50 €/kg 2,51" line
        pending_amount: TextLine | None = None  # a price alone on its row, waiting for its description

        def flush_amount() -> None:
            nonlocal pending_amount
            if pending_amount is not None:
                parsed.append(unresolved_line(pending_amount))
                pending_amount = None

        for line in zone:
            text = _text(line)
            if m := _DISCOUNT.match(text):
                if parsed and not parsed[-1].unresolved:
                    parsed[-1].discount_cents += abs(parse_money_cents(m.group("a1") or m.group("a2")) or 0)
                    parsed[-1].field_confidence["discount"] = self.cap * line.confidence
                    parsed[-1].evidence.append(text)
                continue
            if m := _WEIGHED_LINE.match(text):
                description = m.group("desc") or pending_description
                if description:
                    parsed.append(
                        build_item(
                            description=description,
                            total_text=m.group("total"),
                            qty_text=m.group("qty"),
                            unit_price_text=m.group("unit"),
                            weight_unit=Unit.KG,
                            line_conf=line.confidence,
                            evidence=[description, text],
                        )
                    )
                else:
                    parsed.append(unresolved_line(line))
                pending_description = None
                continue
            if m := _QTY_LINE.match(text):
                if parsed and not parsed[-1].unresolved and _explains(m, parsed[-1]):
                    _apply_quantity(parsed[-1], m, line.confidence, text)
                else:
                    pending_qty = m
                    pending_text = text
                pending_description = None
                continue
            if m := _ITEM.match(text):
                flush_amount()
                item = _item(m, line, text)
                if pending_qty is not None and _explains(pending_qty, item):
                    _apply_quantity(item, pending_qty, line.confidence, pending_text)
                pending_qty = pending_description = None
                parsed.append(item)
            elif _AMOUNT_ONLY.match(text):  # a price alone: the OCR put it on a row of its own
                flush_amount()
                if pending_description:
                    parsed.append(_orphan_item(pending_description, line))
                    pending_description = None
                else:
                    pending_amount = line
                pending_qty = None
            elif re.search(MONEY, text):
                flush_amount()
                parsed.append(unresolved_line(line))
                pending_qty = pending_description = None
            elif re.search(r"[A-Za-zÀ-ÿ]{3}", text):  # a description alone
                description = re.sub(r"^\d{1,2}\s+", "", text)  # "1 COUVE-FLOR": the 1 is a count
                if pending_amount is not None:
                    parsed.append(_orphan_item(description, pending_amount))
                    pending_amount = pending_description = None
                else:
                    pending_description = description
        flush_amount()
        return parsed


def _text(line: TextLine) -> str:
    return line.text.strip()


def _orphan_item(description: str, amount: TextLine) -> ParsedLine:
    """A description and a price that the OCR read on two different rows: they belong together (the price
    sits just above or below its name), but the pairing is a guess, so the line is reviewed."""
    m = _AMOUNT_ONLY.match(_text(amount))
    assert m is not None
    item = build_item(
        description=description,
        total_text=m.group("total"),
        line_conf=amount.confidence,
        evidence=[description, _text(amount)],
    )
    item.field_confidence = {k: min(v, PAIRED_CONFIDENCE) for k, v in item.field_confidence.items()}
    return item


def _item(m: re.Match[str], line: TextLine, text: str) -> ParsedLine:
    desc = re.sub(
        r"^[-–•*.]+\s*", "", _HEADER_NOISE.sub("", m.group("desc")).strip()
    )  # a stray "-" for a misread digit
    if re.fullmatch(r"PRE[CÇ]O|IVA|VALOR|TOTAL", desc, re.I):  # a column title, the item's name is elsewhere
        return unresolved_line(line)
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
    if qty is None and unit:
        total, price = parse_money_cents(m.group("total")), parse_money_cents(unit)
        if total and price and total % price == 0 and total // price > 1:
            # "BIJO 0,12 0,84": the count was lost, 7 × 0,12. A unit price that divides the total exactly
            # is a checksum (a misread digit would not divide), so it is as reliable as the amounts.
            qty = str(total // price)
    if qty is None and unit:
        # Two amounts and no count that explains them: the OCR joined this row with the next one (the
        # price of another product). Taking the last amount would be a silent guess: leave it to review.
        return unresolved_line(line)
    item = build_item(
        description=desc,
        total_text=m.group("total"),
        qty_text=qty,
        unit_price_text=unit if qty else None,
        line_conf=line.confidence,
        evidence=[text],
    )
    _rescore(item, line, lead_is_quantity=bool(lead) and qty == lead)
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
