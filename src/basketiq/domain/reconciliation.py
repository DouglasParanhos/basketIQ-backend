"""Compare printed totals with totals recalculated from the lines (spec técnica §5 passo 8)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol


class LineAmounts(Protocol):
    line_total_cents: int | None
    discount_cents: int


@dataclass
class Reconciliation:
    lines_total_cents: int
    discounts_cents: int
    recalculated_total_cents: int
    printed_total_cents: int | None
    difference_cents: int | None
    within_tolerance: bool
    checks: dict[str, bool] = field(default_factory=dict)


def recalculated_total(lines: Iterable[LineAmounts]) -> tuple[int, int]:
    items = list(lines)
    gross = sum(line.line_total_cents or 0 for line in items)
    discounts = sum(line.discount_cents or 0 for line in items)
    return gross, discounts


def reconcile(
    lines: Iterable[LineAmounts],
    printed_total_cents: int | None,
    tolerance_cents: int,
    subtotal_cents: int | None = None,
    tax_cents: int | None = None,
) -> Reconciliation:
    gross, discounts = recalculated_total(lines)
    net = gross - discounts
    checks: dict[str, bool] = {}
    if printed_total_cents is None:
        diff = None
        ok = False
    else:
        diff = net - printed_total_cents
        ok = abs(diff) <= tolerance_cents
        checks["lines_vs_total"] = ok
    if subtotal_cents is not None and tax_cents is not None and printed_total_cents is not None:
        tax_ok = abs(subtotal_cents + tax_cents - printed_total_cents) <= tolerance_cents
        checks["vat_summary_vs_total"] = tax_ok
        ok = ok and tax_ok
    return Reconciliation(
        lines_total_cents=gross,
        discounts_cents=discounts,
        recalculated_total_cents=net,
        printed_total_cents=printed_total_cents,
        difference_cents=diff,
        within_tolerance=ok,
        checks=checks,
    )
