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

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents
from basketiq.domain.status import Merchant, Unit
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine

LISBON = ZoneInfo("Europe/Lisbon")

# Rule confidences: how much we trust the parser rule itself, multiplied by the text confidence.
RULE_EXACT = 0.98  # value printed explicitly and consistent with the rest of the line
RULE_IMPLICIT_QTY = 0.92  # quantity 1 inferred from a layout where the merchant omits it
RULE_INCONSISTENT = 0.5  # qty × unit price does not match the printed line total
RULE_DESCRIPTION = 0.95

MONEY = r"\d{1,4}[,.]\d{2}"


class Parser(Protocol):
    merchant: Merchant
    version: str

    def parse(self, doc: DocumentText) -> ParsedReceipt: ...


@dataclass
class DateCandidate:
    value: date
    time_: time | None
    source: str


def parse_date_candidates(lines: Iterable[TextLine]) -> list[DateCandidate]:
    """Collect every plausible date/time printed on the receipt. Invalid dates (e.g. an OCR
    misread month 18) are dropped instead of guessed."""
    patterns: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})(?:\s+(?P<H>\d{2}):(?P<M>\d{2}))?"), "iso"),
        (re.compile(r"(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>20\d{2})(?:\s+(?P<H>\d{2}):(?P<M>\d{2}))?"), "dmy"),
        (re.compile(r"\b(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{2})\s+(?P<H>\d{2}):(?P<M>\d{2})"), "dmy2"),
        (re.compile(r"^(?P<y>\d{2})-(?P<m>\d{2})-(?P<d>\d{2})\s+(?P<H>\d{2}):(?P<M>\d{2})"), "ymd2"),
    ]
    found: list[DateCandidate] = []
    for line in lines:
        text = line.text
        for pattern, source in patterns:
            for m in pattern.finditer(text):
                year = int(m.group("y"))
                if year < 100:
                    year += 2000
                try:
                    d = date(year, int(m.group("m")), int(m.group("d")))
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
) -> ParsedLine:
    """Build a ParsedLine from the textual parts of an item and score each field."""
    total = parse_money_cents(total_text)
    qty: Decimal | None
    unit: str
    unit_price: int | None
    if qty_text is not None and unit_price_text is not None:
        qty = parse_decimal(qty_text)
        unit_price = parse_money_cents(unit_price_text)
        # Receipts print weighed quantities with decimals ("0,513") and counts without ("2").
        unit = weight_unit if ("," in qty_text or "." in qty_text) else Unit.UNIT
        consistent = (
            qty is not None
            and unit_price is not None
            and total is not None
            and abs(multiply_cents(qty, unit_price) - total) <= 1
        )
        rule = RULE_EXACT if consistent else RULE_INCONSISTENT
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
    return ParsedLine(
        raw_description=_clean_description(description),
        quantity=qty,
        unit=unit,
        unit_price_cents=unit_price,
        line_total_cents=total,
        tax_code=tax_code,
        field_confidence={k: round(v, 3) for k, v in conf.items()},
        evidence=evidence,
    )


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


def _clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
