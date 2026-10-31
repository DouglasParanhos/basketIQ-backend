"""Business rules of the Home overview (RF-15): ring bands, monthly average, trend, weeks and shares.

Pure functions over whole numbers; written before the code, as the UX v2 plan asks.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from itertools import pairwise

import pytest

from basketiq.domain.overview import (
    average_trend,
    goal_pct,
    monthly_average,
    recent_vs_average,
    ring_band,
    shares,
    week_blocks,
)

GOAL = 40_000


# ---- US1: percentage of the goal and ring band ------------------------------------------------------------


@pytest.mark.req("F-176")
@pytest.mark.parametrize(
    ("spent", "pct", "band"),
    [
        (15_200, 38, "ok"),
        (24_800, 62, "attention"),
        (35_200, 88, "high"),
        (0, 0, "ok"),
        (20_000, 50, "ok"),  # 50 % exactly is still green
        (20_160, 50, "ok"),  # 50,4 %
        (20_200, 51, "attention"),  # 50,5 % rounds up to 51
        (30_000, 75, "attention"),  # 75 % exactly is still orange
        (30_160, 75, "attention"),  # 75,4 %
        (30_200, 76, "high"),  # 75,5 % rounds up to 76
        (40_000, 100, "high"),
        (52_000, 130, "high"),  # past the goal: no limit, no error
    ],
)
def test_goal_percentage_and_band(spent: int, pct: int, band: str) -> None:
    result = goal_pct(spent, GOAL)
    assert result == pct
    assert isinstance(result, int)
    assert ring_band(result, 50, 75) == band


@pytest.mark.req("F-176")
def test_the_band_follows_the_configured_limits() -> None:
    assert ring_band(40, 40, 60) == "ok"
    assert ring_band(41, 40, 60) == "attention"
    assert ring_band(60, 40, 60) == "attention"
    assert ring_band(61, 40, 60) == "high"
    assert ring_band(50, 50, 75) == "ok"  # the same 50 is another band with other limits
    assert ring_band(50, 30, 45) == "high"


@pytest.mark.req("F-176")
def test_percentage_rounds_half_up_with_whole_numbers_only() -> None:
    assert goal_pct(1, 2) == 50
    assert goal_pct(1, 200) == 1  # 0,5 % rounds up
    assert goal_pct(1, 201) == 0  # 0,497 %
    huge = 10**18
    assert goal_pct(huge, 2 * huge) == 50  # a float would lose the exactness well before this


# ---- US2: monthly average and trend ----------------------------------------------------------------------


@pytest.mark.req("F-177")
def test_average_of_two_months_is_in_whole_cents() -> None:
    assert monthly_average([33_600, 30_000], 2) == 31_800  # 300 € and 336 €


@pytest.mark.req("F-177")
@pytest.mark.parametrize(
    ("months", "expected"),
    [
        ([101, 100], 101),  # 100,5 rounds half up
        ([101, 101, 100], 101),  # 100,67
        ([100, 100, 101], 100),  # 100,33
    ],
)
def test_average_rounds_to_the_cent_half_up(months: list[int], expected: int) -> None:
    assert monthly_average(months, 2) == expected


@pytest.mark.req("F-177")
def test_average_needs_the_configured_minimum_of_months() -> None:
    assert monthly_average([], 2) is None
    assert monthly_average([30_000], 2) is None
    assert monthly_average([30_000], 1) == 30_000
    assert monthly_average([30_000, 33_600], 3) is None
    assert monthly_average([30_000, 33_600, 31_000], 3) == 31_533


@pytest.mark.req("F-177")
def test_trend_compares_the_two_most_recent_months_with_the_average() -> None:
    # newest first: 280 € and 260 € against an average of 300 € -> 10 % below
    assert recent_vs_average([28_000, 26_000, 36_000, 30_000]) == -10
    # 340 € and 360 € against 300 € -> 17 % above (16,67 rounds half up)
    assert recent_vs_average([34_000, 36_000, 26_000, 24_000]) == 17
    assert recent_vs_average([30_000, 30_000, 30_000]) == 0


@pytest.mark.req("F-177")
def test_trend_is_symmetric_around_zero_when_rounding() -> None:
    # 1 cent short or over a half point: -7,5 % and +7,5 % both move away from zero
    assert recent_vs_average([9_250, 9_250, 11_500]) == -8  # average 10_000, recent 9_250 -> -7,5 %
    assert recent_vs_average([10_750, 10_750, 8_500]) == 8  # average 10_000, recent 10_750 -> +7,5 %


@pytest.mark.req("F-177")
def test_trend_is_empty_below_three_months_and_without_spending() -> None:
    assert recent_vs_average([]) is None
    assert recent_vs_average([30_000]) is None
    assert recent_vs_average([30_000, 33_600]) is None  # with two months it would always be 0 %
    assert recent_vs_average([0, 0, 0]) is None


@pytest.mark.req("F-177")
@pytest.mark.parametrize(
    ("pct", "trend"),
    [
        (-6, "below"),
        (-5, "on_average"),
        (-4, "on_average"),
        (0, "on_average"),
        (5, "on_average"),
        (6, "above"),
    ],
)
def test_on_the_average_has_a_configured_margin(pct: int, trend: str) -> None:
    assert average_trend(pct, 5) == trend


@pytest.mark.req("F-177")
def test_trend_label_is_empty_when_the_trend_is() -> None:
    assert average_trend(None, 5) is None
    assert average_trend(1, 0) == "above"  # a margin of 0 leaves only 0 % «on the average»
    assert average_trend(0, 0) == "on_average"


# ---- US3: weeks and shares -------------------------------------------------------------------------------


@pytest.mark.req("F-178")
def test_a_28_day_month_has_four_weeks() -> None:
    blocks = week_blocks(2027, 2)
    assert len(blocks) == 4
    assert blocks[0] == (date(2027, 2, 1), date(2027, 2, 7))
    assert blocks[-1] == (date(2027, 2, 22), date(2027, 2, 28))


@pytest.mark.req("F-178")
def test_a_31_day_month_has_a_last_week_of_three_days() -> None:
    blocks = week_blocks(2026, 10)
    assert len(blocks) == 5
    assert blocks[3] == (date(2026, 10, 22), date(2026, 10, 28))
    assert blocks[4] == (date(2026, 10, 29), date(2026, 10, 31))


@pytest.mark.req("F-178")
def test_a_leap_february_has_a_last_week_of_one_day() -> None:
    blocks = week_blocks(2028, 2)
    assert len(blocks) == 5
    assert blocks[4] == (date(2028, 2, 29), date(2028, 2, 29))


@pytest.mark.req("F-178")
def test_weeks_cover_every_day_of_the_month_once() -> None:
    for month in range(1, 13):
        blocks = week_blocks(2026, month)
        assert blocks[0][0] == date(2026, month, 1)
        assert blocks[-1][1] == date(2026, month, monthrange(2026, month)[1])
        assert all(a[1] + timedelta(days=1) == b[0] for a, b in pairwise(blocks))


@pytest.mark.req("F-178")
def test_shares_always_add_up_to_one_hundred() -> None:
    assert shares([100, 100, 100]) == [34, 33, 33]  # the first of equal remainders gets the extra point
    assert shares([100]) == [100]
    assert shares([]) == []
    assert shares([0, 0]) == []
    for values in (
        [5, 3, 2],
        [1, 1, 1, 1, 1, 1, 1],
        [9_999, 1],
        [333, 333, 334],
        [1, 2, 3, 4, 5, 6, 7, 8, 9],
    ):
        assert sum(shares(values)) == 100
        assert len(shares(values)) == len(values)


@pytest.mark.req("F-178")
def test_shares_give_the_extra_points_to_the_largest_remainders() -> None:
    # 62,5 / 25 / 12,5 -> floors 62, 25, 12 (sum 99); the remainders are .5, 0 and .5, the first .5 wins
    assert shares([5, 2, 1]) == [63, 25, 12]
    # 70 % exactly keeps its value
    assert shares([7, 3]) == [70, 30]
