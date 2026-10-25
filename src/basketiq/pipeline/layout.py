"""Group positioned tokens (PDF words or OCR boxes) into visual lines."""

from __future__ import annotations

from itertools import pairwise
from statistics import median

from basketiq.pipeline.schema import AMOUNT_TOKEN, TextLine, TextToken

_MAX_SKEW = 0.1  # dy/dx, about 5.7 degrees
_MIN_LONG_TOKEN = 3  # a token this many heights wide has a reliable baseline angle
_MIN_RELIABLE = 3  # long tokens needed nearby before their angle is trusted
_NEIGHBOURHOOD_ROWS = 8  # vertical reach of the local tilt, in median token heights


def row_corrections(tokens: list[TextToken]) -> list[float]:
    """Vertical shift per token that undoes the tilt of a photographed receipt.

    The OCR reports the baseline angle of every detected text. Short texts ("5,36") give a noisy
    angle, long ones a reliable one, so the local tilt is the median angle of the long texts at about
    the same height on the page. A curled receipt is tilted one way near the top and the other near the
    bottom, which is why the tilt is local. Without OCR angles (PDF text) or with too few long texts
    nothing is corrected."""
    if not tokens:
        return []
    height = median(t.y1 - t.y0 for t in tokens)
    reliable = [
        ((t.y0 + t.y1) / 2, t.slope)
        for t in tokens
        if t.slope is not None and t.x1 - t.x0 >= _MIN_LONG_TOKEN * height
    ]
    if len(reliable) < _MIN_RELIABLE:
        return [0.0] * len(tokens)
    reach = height * _NEIGHBOURHOOD_ROWS / 2
    x_ref = median((t.x0 + t.x1) / 2 for t in tokens)
    corrections = []
    for t in tokens:
        y = (t.y0 + t.y1) / 2
        near = [s for cy, s in reliable if abs(cy - y) <= reach]
        slope = median(near) if len(near) >= _MIN_RELIABLE else 0.0
        slope = max(-_MAX_SKEW, min(_MAX_SKEW, slope))
        corrections.append(-slope * ((t.x0 + t.x1) / 2 - x_ref))
    return corrections


_MIN_AMOUNTS = 6  # amounts nearby needed to judge whether the tilt explains where they sit (fewer is noise)
_MAX_TILT_RESIDUAL = 0.2  # of a row pitch: how far the tilt may be from what is observed and still be trusted
_JUDGEMENT_ROWS = 4  # rows above and below a token whose amounts decide whether its tilt is trusted
_COLUMN_WIDTH = 0.1  # of the page width: tokens starting this close to the left edge are in its first column


def _row_pitch(tokens: list[TextToken]) -> float | None:
    """Distance between consecutive rows: the median gap between the centres of different rows, taken
    on the left-most column, where the descriptions start, so the offset of the amounts does not count."""
    height = median(t.y1 - t.y0 for t in tokens)
    left = min(t.x0 for t in tokens)
    width = max(t.x1 for t in tokens) - left
    column = [t for t in tokens if t.x0 - left <= _COLUMN_WIDTH * width]
    centres = sorted((t.y0 + t.y1) / 2 for t in column)
    gaps = [b - a for a, b in pairwise(centres) if b - a > height / 2]
    return median(gaps) if gaps else None


def _tilt_residuals(
    tokens: list[TextToken], corrections: list[float], pitch: float
) -> list[tuple[float, float]]:
    """(height on the page, residual) for every amount: how far the tilt is from what is observed.

    Without correction an amount is nearest to some description on its left, `observed` apart in height.
    If the rows are tilted the way the OCR reports, the amount should sit `expected` away from its own
    description (the shift the correction applies between the two). The two agree up to whole rows, since
    any row is a candidate partner; what is left is the residual."""
    centres = [(t.y0 + t.y1) / 2 for t in tokens]
    is_amount = [bool(AMOUNT_TOKEN.match(t.text.strip())) for t in tokens]
    texts = [i for i, amount in enumerate(is_amount) if not amount]
    residuals = []
    for a in (i for i, amount in enumerate(is_amount) if amount):
        left = [t for t in texts if tokens[t].x1 <= tokens[a].x0 + 1]
        if not left:
            continue
        nearest = min(left, key=lambda t: abs(centres[a] - centres[t]))
        observed = centres[a] - centres[nearest]
        expected = corrections[nearest] - corrections[a]
        residuals.append((centres[a], abs((observed - expected + pitch / 2) % pitch - pitch / 2)))
    return residuals


def _tilt_corrections(tokens: list[TextToken]) -> list[float]:
    """The tilt correction, except where the positions of the amounts contradict it.

    The angles the OCR reports are not always the tilt of the rows: on a long receipt read in slices
    they can disagree with where the amounts actually sit, and trusting them then moves every amount
    next to the wrong description (a price silently belongs to the item above or below). Each stretch of
    the receipt is judged by the amounts around it; where there are too few to judge, the tilt stays."""
    corrections = row_corrections(tokens)
    pitch = _row_pitch(tokens) if any(corrections) else None
    if not pitch:
        return corrections
    residuals = _tilt_residuals(tokens, corrections, pitch)
    reach = _JUDGEMENT_ROWS * pitch
    trusted = []
    for token, correction in zip(tokens, corrections, strict=True):
        y = (token.y0 + token.y1) / 2
        near = [r for cy, r in residuals if abs(cy - y) <= reach]
        contradicted = len(near) >= _MIN_AMOUNTS and median(near) > _MAX_TILT_RESIDUAL * pitch
        trusted.append(0.0 if contradicted else correction)
    return trusted


def group_into_lines(tokens: list[TextToken], overlap_ratio: float = 0.5) -> list[TextLine]:
    """A token belongs to a line when its centre is within `1 - overlap_ratio` of the smaller text
    height from the centre of the line. Lines are returned top to bottom, tokens left to right.

    Centres are measured after removing the local tilt, so a slightly rotated or curled photo does not
    merge neighbouring rows (the right-hand amounts sit higher or lower than their descriptions). They are
    compared with the line centre, not with the span the line has grown to: a span grows with every
    token and would end up swallowing the rows below."""
    lines: list[TextLine] = []
    for page in sorted({t.page for t in tokens}):
        page_tokens = [t for t in tokens if t.page == page and t.text.strip()]
        # (token, vertical correction); the tokens themselves keep their original coordinates.
        shifted = sorted(
            zip(page_tokens, _tilt_corrections(page_tokens), strict=True),
            key=lambda p: (p[0].y0 + p[0].y1) / 2 + p[1],
        )
        current: list[TextToken] = []
        centres: list[float] = []
        for token, dy in shifted:
            centre = (token.y0 + token.y1) / 2 + dy
            if current:
                reach = (1 - overlap_ratio) * min(token.y1 - token.y0, median(t.y1 - t.y0 for t in current))
                if abs(centre - median(centres)) <= reach:
                    current.append(token)
                    centres.append(centre)
                    continue
                lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
            current, centres = [token], [centre]
        if current:
            lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
    return lines
