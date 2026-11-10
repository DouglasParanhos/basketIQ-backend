"""The price shown for a product on the shopping list: the last one the person paid.

Kept in one place so the rule can change without touching the rest (a promotion price, a shared price base).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from basketiq.domain.money import multiply_cents, to_base_unit


@dataclass(frozen=True)
class PricePoint:
    """What was paid for one unit of a product (the base unit of the line), net of discounts."""

    cents: int
    basis: str  # un, kg or l
    day: date
    merchant: str | None  # supported merchant id; None for any other store
    merchant_name: str


# Key of a store: the supported merchant id (or None) and its name, as `merchant_label` gives them.
Store = tuple[str | None, str]


def expected_price(
    selected: str | None, last_by_store: Mapping[Store, PricePoint]
) -> tuple[PricePoint, bool] | None:
    """The last price paid at the selected supermarket; failing that, the most recent one paid anywhere else.

    Returns the price and whether it comes from another supermarket, or None if it was never bought.
    """
    for (merchant, _name), point in last_by_store.items():
        if merchant is not None and merchant == selected:
            return point, False
    if not last_by_store:
        return None
    return max(last_by_store.values(), key=lambda p: (p.day, p.merchant_name)), True


def cost_cents(quantity: Decimal, unit: str | None, price: PricePoint) -> int | None:
    """What `quantity` of the item costs at `price`, in the price's unit (g→kg, ml→l; no unit is a count).

    None when the units do not compare (2 «un» against a price per kg): never a guess (constitution III).
    """
    amount, basis = to_base_unit(quantity, unit)
    return multiply_cents(amount, price.cents) if basis == price.basis else None
