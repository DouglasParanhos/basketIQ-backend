"""Group positioned tokens (PDF words or OCR boxes) into visual lines."""

from __future__ import annotations

from basketiq.pipeline.schema import TextLine, TextToken


def group_into_lines(tokens: list[TextToken], overlap_ratio: float = 0.5) -> list[TextLine]:
    """Tokens whose vertical spans overlap by at least `overlap_ratio` of the smaller height
    belong to the same line. Lines are returned top to bottom, tokens left to right."""
    lines: list[TextLine] = []
    for page in sorted({t.page for t in tokens}):
        page_tokens = sorted(
            (t for t in tokens if t.page == page and t.text.strip()), key=lambda t: (t.y0 + t.y1) / 2
        )
        current: list[TextToken] = []
        top = bottom = 0.0
        for token in page_tokens:
            if current and _overlaps(top, bottom, token.y0, token.y1, overlap_ratio):
                current.append(token)
                top, bottom = min(top, token.y0), max(bottom, token.y1)
                continue
            if current:
                lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
            current, top, bottom = [token], token.y0, token.y1
        if current:
            lines.append(TextLine(tokens=sorted(current, key=lambda t: t.x0), page=page))
    return lines


def _overlaps(a0: float, a1: float, b0: float, b1: float, ratio: float) -> bool:
    overlap = min(a1, b1) - max(a0, b0)
    smaller = max(min(a1 - a0, b1 - b0), 1e-6)
    return overlap / smaller >= ratio
