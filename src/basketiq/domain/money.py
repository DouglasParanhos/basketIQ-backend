"""Exact money and quantity handling. Money is always integer cents; never floats."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from basketiq.domain.status import Unit

# Receipts always print two decimals. A dot is accepted as decimal separator because OCR
# frequently reads the comma as a dot; dots followed by three digits are thousands separators.
_MONEY_RE = re.compile(r"^\(?\s*([+-])?\s*(\d{1,3}(?:\.\d{3})+|\d+)[,.](\d{2})\s*\)?$")


def parse_money_cents(text: str) -> int | None:
    """Parse a Portuguese-formatted amount ("1.234,56", "3,49", "(0,60)", "-0,50") into cents.

    A value wrapped in parentheses is negative, as printed by Pingo Doce for savings.
    Returns None when the text is not an amount.
    """
    raw = text.strip().replace("EUR", "").replace("€", "").strip()
    m = _MONEY_RE.match(raw)
    if not m:
        return None
    sign, whole, frac = m.groups()
    cents = int(whole.replace(".", "")) * 100 + int(frac)
    if (raw.startswith("(") and raw.endswith(")")) or sign == "-":
        cents = -cents
    return cents


def parse_decimal(text: str) -> Decimal | None:
    """Parse a quantity like "0,513" or "2" or "1.5" into a Decimal."""
    raw = text.strip().replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def format_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100},{cents % 100:02d} €"


def multiply_cents(quantity: Decimal, unit_price_cents: int) -> int:
    """quantity × unit price, rounded half-up to the cent (as printed on receipts)."""
    return int((quantity * Decimal(unit_price_cents)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_base_unit(quantity: Decimal, unit: str | None) -> tuple[Decimal, str]:
    """Convert g→kg and ml→l so average prices use a single base per product."""
    if unit == Unit.G:
        return quantity / Decimal(1000), Unit.KG
    if unit == Unit.ML:
        return quantity / Decimal(1000), Unit.L
    return quantity, unit or Unit.UNIT
