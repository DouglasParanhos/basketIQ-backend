"""One pixel budget for the whole pipeline. Imports nothing from the pipeline, so any module can use it."""

from __future__ import annotations

import math

MAX_PIXELS = 60_000_000  # an uploaded photo (~ 8000x7500); larger is not a phone photo
WORK_PIXELS = 25_000_000  # any image the pipeline creates (A4 at 200 dpi is 3.9 M, 1400x5600 is 7.8 M)


def fits(width: int, height: int, budget: int = MAX_PIXELS) -> bool:
    return width * height <= budget


def fit_scale(width: int, height: int, scale: float, budget: int | None = None) -> float:
    """`scale`, lowered if needed so that the scaled image stays within the budget (`WORK_PIXELS` by default,
    read at call time)."""
    limit = WORK_PIXELS if budget is None else budget
    return min(scale, math.sqrt(limit / max(1, width * height)))


def scaled_size(width: int, height: int, scale: float) -> tuple[int, int]:
    """The size of a `width` x `height` image scaled by `scale`, lowered to fit `WORK_PIXELS`."""
    factor = fit_scale(width, height, scale)
    return max(1, round(width * factor)), max(1, round(height * factor))
