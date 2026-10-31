"""Shared building blocks for the versioned per-merchant parsers."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from basketiq.domain.money import multiply_cents, one_digit_apart, parse_decimal, parse_money_cents
from basketiq.domain.status import Merchant, Unit
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

LISBON = ZoneInfo("Europe/Lisbon")

# Rule confidences: how much we trust the parser rule itself, multiplied by the text confidence.
RULE_EXACT = 0.98  # value printed explicitly and consistent with the rest of the line
RULE_IMPLICIT_QTY = 0.92  # quantity 1 inferred from a layout where the merchant omits it
RULE_INCONSISTENT = 0.5  # qty × unit price does not match the printed line total
RULE_DESCRIPTION = 0.95

MONEY = r"\d{1,4}[,.]\d{2}"

UNIT_ALERT_CONFIDENCE = 0.5  # a unit in doubt: below every review threshold
COUNT_INFERRED_CONFIDENCE = 0.5  # a count that was worked out from the amounts, not printed: reviewed
_WHOLE_WEIGHT = re.compile(r"\d+[,.]000")

DEPOSIT_CENTS = 10  # current deposit per bottle in the Portuguese return scheme
_DEPOSIT = re.compile(r"dep[oó]sito\b|vasilhame\b", re.I)


class Parser(Protocol):
    merchant: Merchant
    version: str

    def parse(self, doc: DocumentText) -> ParsedReceipt: ...


@dataclass
class DateCandidate:
    value: date
    time_: time | None
    source: str


_PT_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"), start=1
    )
}
_PT_MONTH = "|".join(name.replace("o", "[o0]") for name in _PT_MONTHS)


def parse_date_candidates(lines: Iterable[TextLine]) -> list[DateCandidate]:
    """Collect every plausible date/time printed on the receipt. Invalid dates (e.g. an OCR
    misread month 18) are dropped instead of guessed."""
    patterns: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})(?:\s+(?P<H>\d{2}):(?P<M>\d{2}))?"), "iso"),
        (re.compile(r"(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>20\d{2})(?:\s+(?P<H>\d{2}):(?P<M>\d{2}))?"), "dmy"),
        (re.compile(r"\b(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{2})\s+(?P<H>\d{2}):(?P<M>\d{2})"), "dmy2"),
        # The OCR sometimes doubles a dash ("26--10-24").
        (
            re.compile(r"^(?P<y>\d{2})-{1,2}(?P<m>\d{2})-{1,2}(?P<d>\d{2})\s+(?P<H>\d{2}):(?P<M>\d{2})"),
            "ymd2",
        ),
        # "24/out/26 16:50" (El Corte Inglés); the OCR may read the "o" of "out" as a zero.
        (
            re.compile(
                rf"\b(?P<d>\d{{2}})/(?P<m>{_PT_MONTH})/(?P<y>\d{{2}})\s+(?P<H>\d{{2}}):(?P<M>\d{{2}})", re.I
            ),
            "dmy_pt",
        ),
    ]
    found: list[DateCandidate] = []
    for line in lines:
        text = line.text
        for pattern, source in patterns:
            for m in pattern.finditer(text):
                year = int(m.group("y"))
                if year < 100:
                    year += 2000
                month = m.group("m")
                month_number = int(month) if month.isdigit() else _PT_MONTHS[month.lower().replace("0", "o")]
                try:
                    d = date(year, month_number, int(m.group("d")))
                except ValueError:
                    continue
                t: time | None = None
                if m.groupdict().get("H"):
                    try:
                        t = time(int(m.group("H")), int(m.group("M")))
                    except ValueError:
                        t = None
                found.append(DateCandidate(d, t, source))
    return found


def choose_purchase_datetime(candidates: list[DateCandidate]) -> tuple[datetime | None, float]:
    """Pick the date most sources agree on; confidence reflects corroboration."""
    if not candidates:
        return None, 0.0
    counts = Counter(c.value for c in candidates)
    best_date, votes = counts.most_common(1)[0]
    conflicting = len(counts) > 1
    times = [c.time_ for c in candidates if c.value == best_date and c.time_ is not None]
    chosen_time = min(times) if times else time(0, 0)
    dt = datetime.combine(best_date, chosen_time, tzinfo=LISBON)
    if votes >= 2 and not conflicting:
        conf = 0.99
    elif votes >= 2:
        conf = 0.9
    elif conflicting:
        conf = 0.5
    else:
        conf = 0.85
    if not times:
        conf = min(conf, 0.85)
    return dt, conf


def build_item(
    *,
    description: str,
    total_text: str,
    line_conf: float,
    evidence: list[str],
    qty_text: str | None = None,
    unit_price_text: str | None = None,
    tax_code: str | None = None,
    weight_unit: str = Unit.KG,
    implicit_qty_rule: float = RULE_IMPLICIT_QTY,
    unit_printed: bool = False,
) -> ParsedLine:
    """Build a ParsedLine from the textual parts of an item and score each field. `unit_printed` says the
    line writes the unit ("kg") next to the quantity."""
    total = parse_money_cents(total_text)
    qty: Decimal | None
    unit: str
    unit_price: int | None
    if qty_text is not None and unit_price_text is not None:
        qty = parse_decimal(qty_text)
        unit_price = parse_money_cents(unit_price_text)
        unit = weight_unit if is_weight_quantity(qty_text) else Unit.UNIT
        consistent = (
            qty is not None
            and unit_price is not None
            and total is not None
            and abs(multiply_cents(qty, unit_price) - total) <= 1
        )
        rule = RULE_EXACT if consistent else RULE_INCONSISTENT
        if not consistent and qty is not None and qty == qty.to_integral_value():
            # "2 X 2,60 5,36": the line total is what the receipt adds up, so it stands; the line is reviewed.
            unit_price = _misread_unit_price(total, int(qty), unit_price) or unit_price
        conf = {
            "quantity": rule * line_conf,
            "unit_price": rule * line_conf,
            "line_total": (RULE_EXACT if consistent else RULE_INCONSISTENT) * line_conf,
        }
    else:
        qty, unit, unit_price = Decimal(1), Unit.UNIT, total
        conf = {
            "quantity": implicit_qty_rule * line_conf,
            "unit_price": implicit_qty_rule * line_conf,
            "line_total": RULE_EXACT * line_conf,
        }
    conf["description"] = RULE_DESCRIPTION * line_conf
    if total is None:
        conf["line_total"] = 0.0
    item = ParsedLine(
        raw_description=_clean_description(description),
        quantity=qty,
        unit=unit,
        unit_price_cents=unit_price,
        line_total_cents=total,
        tax_code=tax_code,
        field_confidence={k: round(v, 3) for k, v in conf.items()},
        evidence=evidence,
    )
    if qty_text is not None and unit_price_text is not None:
        flag_ambiguous_unit(item, qty_text, unit_printed)
    return item


def one_letter_apart(a: str, b: str) -> bool:
    """Two different words, one letter read as another, added or lost ("CESCONTO" for "DESCONTO")."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def _misread_unit_price(total: int | None, count: int, unit: int | None) -> int | None:
    """total / count, when it is the printed unit price with one digit misread (2,68 read as 2,60)."""
    if not total or not unit or count < 1 or total % count:
        return None
    return total // count if one_digit_apart(total // count, unit) else None


def count_from_total(total: int | None, unit: int | None) -> int | None:
    """The count of a line that the OCR lost ("X 2,68 5,36"): total / unit price when it divides into a
    whole count, or else the nearest whole count when total / count is the printed unit price with one digit
    misread ("X 2,60 5,36" is 2 x 2,68; `build_item` then corrects the price). None when neither holds."""
    if not total or not unit or total < 0 or unit < 0:
        return None
    if total % unit == 0:
        return total // unit
    count = round(total / unit)
    return count if count >= 2 and _misread_unit_price(total, count, unit) else None


def apply_deposit_quantities(lines: Iterable[ParsedLine]) -> None:
    """Packaging deposit lines ("Depósito 0.10 0,60 F", Portuguese "Volta" scheme) print no quantity:
    the total is bottles × DEPOSIT_CENTS. Unresolved lines are left alone, they stay in review."""
    for line in lines:
        if line.unresolved or line.line_total_cents is None or not _DEPOSIT.match(line.raw_description):
            continue
        bottles, remainder = divmod(line.line_total_cents, DEPOSIT_CENTS)
        line.quantity = Decimal(max(bottles + (remainder * 2 >= DEPOSIT_CENTS), 1))
        line.unit = Unit.UNIT
        line.unit_price_cents = DEPOSIT_CENTS
        # A total that is not a whole number of deposits is rounded but must still be reviewed.
        conf = RULE_INCONSISTENT if remainder else line.field_confidence.get("line_total", 0.0)
        line.field_confidence["quantity"] = line.field_confidence["unit_price"] = round(conf, 3)


def flag_ambiguous_unit(item: ParsedLine, qty_text: str | None, unit_printed: bool = False) -> None:
    """ "4,000 X 0,69": a quantity printed like a weighed one but with a whole value. Units or kg? Nothing in
    the line says, so the unit is put in doubt and the line goes to review (spec 009, D1b). A unit printed
    next to the quantity ("1,000 kg") settles it, and so does a real weight ("1,004")."""
    if qty_text and not unit_printed and _WHOLE_WEIGHT.fullmatch(qty_text.strip()):
        item.field_confidence["unit"] = UNIT_ALERT_CONFIDENCE


def unresolved_line(line: TextLine) -> ParsedLine:
    """A line inside the items area that no rule understood. Kept for review (never dropped)."""
    amounts = re.findall(MONEY, line.text)
    total = parse_money_cents(amounts[-1]) if amounts else None
    return ParsedLine(
        raw_description=_clean_description(line.text),
        quantity=None,
        unit=None,
        unit_price_cents=None,
        line_total_cents=total,
        field_confidence={"description": 0.3, "quantity": 0.0, "unit_price": 0.0, "line_total": 0.3},
        evidence=[line.text],
        unresolved=True,
    )


def parse_tax_table(lines: Iterable[TextLine], base_col: int, tax_col: int) -> tuple[int | None, int | None]:
    """Sum the VAT summary rows ("C 6% 7,70 0,46 8,16"). Columns are amount positions."""
    base_total = tax_total = 0
    rows = 0
    row_re = re.compile(rf"^[A-Z]\s*\d{{1,2}}\s*%((?:\s+{MONEY}){{3}})\s*$")
    for line in lines:
        m = row_re.match(line.text.strip())
        if not m:
            continue
        amounts = [parse_money_cents(a) for a in m.group(1).split()]
        if any(a is None for a in amounts):
            continue
        base_total += amounts[base_col] or 0
        tax_total += amounts[tax_col] or 0
        rows += 1
    if rows == 0:
        return None, None
    return base_total, tax_total


def find_amount(lines: Iterable[TextLine], pattern: re.Pattern[str]) -> tuple[int | None, float, str | None]:
    for line in lines:
        m = pattern.match(line.text.strip())
        if m:
            cents = parse_money_cents(m.group("amount"))
            if cents is not None:
                return cents, line.confidence, line.text
    return None, 0.0, None


def index_of(lines: list[TextLine], predicate: Callable[[str], bool], start: int = 0) -> int | None:
    """Index of the first line (from `start`) whose stripped text satisfies `predicate`."""
    for i in range(start, len(lines)):
        if predicate(lines[i].text.strip()):
            return i
    return None


def is_weight_quantity(qty_text: str, kg_printed: bool = False) -> bool:
    """Weighed quantities are printed with decimals ("0,513", "1,004 kg"), counts without ("2"). A whole value
    printed like a weight ("4,000 X 0,69", the Pingo Doce bakery) is read as a count unless "kg" is printed:
    a scale stops on a whole kilo about once in a thousand weighings. The unit alert still asks (D1b)."""
    text = qty_text.strip()
    return kg_printed or (("," in text or "." in text) and not _WHOLE_WEIGHT.fullmatch(text))


_NOT_A_STORE = re.compile(
    r"\b(TAXA|TOTAL|IVA|NIF|NIPC|TEL|CART[AÃ]O|COMPRA|VISA|MULTIBANCO|TERMINAL|PAGAMENTO)\b", re.I
)
_TERMINAL = re.compile(r"^Terminal\s+Pagamento", re.I)


def terminal_store_label(lines: list[TextLine]) -> str | None:
    """Store name from the card-terminal block ("ALDI ARDEIRA / CHARNECA CAPARICA"), which most
    Portuguese receipts print right above "Terminal Pagamento Automático"."""
    anchor = index_of(lines, lambda t: bool(_TERMINAL.match(t)))
    if anchor is None:
        return None
    found: list[str] = []
    for line in reversed(lines[max(0, anchor - 2) : anchor]):
        text = _clean_description(line.text)
        if len(re.findall(r"[A-Za-zÀ-ÿ]", text)) < 3 or re.search(MONEY, text) or _NOT_A_STORE.search(text):
            break
        found.append(text)
    return " - ".join(reversed(found)) or None


_NOT_A_NAME = re.compile(
    r"\b(FATURA|FACTURA|NIF|NIPC|CONTRIBUINTE|ORIGINAL|DUPLICADO|TEL|TELEFONE|CAPITAL|HOR[AÁ]RIO|SEDE|"
    r"REGISTO|DATA|SIMPLIFICADA|OBRIGAD[OA]|DENOMINA|DESCRI)\b|WWW\.|\.PT\b|\.COM\b|\d{5,}",
    re.I,
)


def guess_merchant_name(lines: list[TextLine], search: int = 8) -> str | None:
    """Name of a store without a parser: the first header line that reads like a name."""
    for line in lines[:search]:
        text = _clean_description(line.text)
        letters = len(re.findall(r"[A-Za-zÀ-ÿ]", text))
        if letters >= 3 and letters >= 0.6 * len(text) and len(text) <= 40 and not _NOT_A_NAME.search(text):
            return text
    return None


def line_before(lines: list[TextLine], predicate: Callable[[str], bool]) -> str | None:
    """Last non-empty line before the first line satisfying `predicate`: the store name that
    Pingo Doce and Continente print right above the company details."""
    anchor = index_of(lines, predicate)
    if anchor is None:
        return None
    return next((text for line in reversed(lines[:anchor]) if (text := _clean_description(line.text))), None)


def _clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
