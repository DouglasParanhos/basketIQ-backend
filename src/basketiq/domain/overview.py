"""Business rules of the Home overview (RF-15). Whole numbers only, rounded half up; never floats.

Month totals are the spending of complete months that have confirmed receipts, newest first.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

Band = Literal["ok", "attention", "high"]
Trend = Literal["below", "on_average", "above"]

WEEK_DAYS = 7
# The trend compares this many recent months with the average, so it needs one more month than this.
TREND_MONTHS = 2


def goal_pct(spent_cents: int, goal_cents: int) -> int:
    """Share of the goal already spent, as a whole percentage (half up); may exceed 100."""
    return (spent_cents * 100 + goal_cents // 2) // goal_cents


def ring_band(pct: int, green_up_to: int, orange_up_to: int) -> Band:
    """Band of the goal ring, decided on the rounded percentage the screen shows."""
    if pct <= green_up_to:
        return "ok"
    if pct <= orange_up_to:
        return "attention"
    return "high"


def _div_half_up(numerator: int, denominator: int) -> int:
    """numerator / denominator rounded to the nearest integer, halves away from zero (denominator > 0)."""
    magnitude = (2 * abs(numerator) + denominator) // (2 * denominator)
    return -magnitude if numerator < 0 else magnitude


def monthly_average(month_totals: Sequence[int], min_months: int) -> int | None:
    """Average spending per month in cents, or None below the minimum number of months."""
    if len(month_totals) < min_months:
        return None
    return _div_half_up(sum(month_totals), len(month_totals))


def recent_vs_average(month_totals: Sequence[int]) -> int | None:
    """Whole percentage by which the two most recent months differ from the average (negative = below).

    With only two months the two most recent are all of them and the answer would always be 0 %, so
    fewer than three months give None.
    """
    n = len(month_totals)
    total = sum(month_totals)
    if n <= TREND_MONTHS or total <= 0:
        return None
    recent = sum(month_totals[:TREND_MONTHS])
    # (recent / 2 - total / n) / (total / n) * 100, kept in whole numbers.
    return _div_half_up((recent * n - TREND_MONTHS * total) * 100, TREND_MONTHS * total)


def average_trend(pct: int | None, on_target_pct: int) -> Trend | None:
    """Whether the trend is «on the average», within the configured margin."""
    if pct is None:
        return None
    if pct < -on_target_pct:
        return "below"
    if pct > on_target_pct:
        return "above"
    return "on_average"


def week_blocks(year: int, month: int) -> list[tuple[date, date]]:
    """Blocks of 7 days from day 1 (1–7, 8–14, …); the last one holds what is left of the month."""
    last = monthrange(year, month)[1]
    blocks = []
    for first in range(1, last + 1, WEEK_DAYS):
        end = min(first + WEEK_DAYS - 1, last)
        blocks.append((date(year, month, first), date(year, month, 1) + timedelta(days=end - 1)))
    return blocks


def shares(values: Sequence[int]) -> list[int]:
    """Whole percentages of the values that add up to 100 (largest remainder; ties go to the first)."""
    total = sum(values)
    if total <= 0:
        return []
    floors = [v * 100 // total for v in values]
    remainders = [v * 100 % total for v in values]
    missing = 100 - sum(floors)
    for i in sorted(range(len(values)), key=lambda i: (-remainders[i], i))[:missing]:
        floors[i] += 1
    return floors
