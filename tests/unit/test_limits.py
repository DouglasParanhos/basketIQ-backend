from __future__ import annotations

import pytest

from basketiq.pipeline.limits import MAX_PIXELS, WORK_PIXELS, fit_scale, fits, scaled_size


@pytest.mark.req("SEC-limits")
def test_fits_is_a_pixel_count_not_a_side_length() -> None:
    assert fits(8000, 7000)
    assert not fits(10_000, 10_000)
    assert fits(1, MAX_PIXELS) and not fits(1, MAX_PIXELS + 1)
    assert fits(5000, 5000, WORK_PIXELS) and not fits(5001, 5000, WORK_PIXELS)


@pytest.mark.req("SEC-limits")
def test_fit_scale_never_enlarges_past_the_budget() -> None:
    scale = fit_scale(300, 100_000, 3.0)
    assert 300 * scale * 100_000 * scale <= WORK_PIXELS * 1.001
    assert scale < 3.0


@pytest.mark.req("SEC-limits")
def test_fit_scale_leaves_small_cases_alone() -> None:
    assert fit_scale(595, 842, 200 / 72) == 200 / 72
    assert fit_scale(900, 3000, 1.5) == 1.5


@pytest.mark.req("SEC-limits")
def test_scaled_size_rounds_and_never_collapses() -> None:
    assert scaled_size(300, 2000, 3.0) == (900, 6000)
    assert scaled_size(1, 1, 0.0001) == (1, 1)
