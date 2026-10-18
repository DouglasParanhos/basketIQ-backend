"""Group positioned tokens (PDF words or OCR boxes) into visual lines."""

from __future__ import annotations

from statistics import median

from basketiq.pipeline.schema import TextLine, TextToken

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


def group_into_lines(tokens: list[TextToken], overlap_ratio: float = 0.5) -> list[TextLine]:
    """Tokens whose vertical spans overlap by at least `overlap_ratio` of the smaller height
    belong to the same line. Lines are returned top to bottom, tokens left to right.

    Spans are measured after removing the local tilt, so a slightly rotated or curled photo does not
    merge neighbouring rows (the right-hand amounts sit higher or lower than their descriptions)."""
    lines: list[TextLine] = []
    for page in sorted({t.page for t in tokens}):
        page_tokens = [t for t in tokens if t.page == page and t.text.strip()]
        # (token, vertical correction); the tokens themselves keep their original coordinates.
        shifted = sorted(
            zip(page_tokens, row_corrections(page_tokens), strict=True),
            key=lambda p: (p[0].y0 + p[0].y1) / 2 + p[1],
        )
        current: list[TextToken] = []
        top = bottom = 0.0
        for token, dy in shifted:
            y0, y1 = token.y0 + dy, token.y1 + dy
            if current and _overlaps(top, bottom, y0, y1, overlap_ratio):
                current.append(token)
                top, bottom = min(top, y0), max(bottom, y1)
                continue
            if current:
                lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
            current, top, bottom = [token], y0, y1
        if current:
            lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
    return lines


def _overlaps(a0: float, a1: float, b0: float, b1: float, ratio: float) -> bool:
    overlap = min(a1, b1) - max(a0, b0)
    smaller = max(min(a1 - a0, b1 - b0), 1e-6)
    return overlap / smaller >= ratio
