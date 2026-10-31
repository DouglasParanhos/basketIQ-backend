"""Reading the paper of a page with the OCR engine, slice by slice, and the extra looks a small receipt gets.

On a receipt photographed with few pixels (narrower than `quality.MIN_PAPER_WIDTH`, e.g. a photo sent through
a messaging app) the text is a few pixels high. The detector then misses whole rows of prices, a different set
at each scale, so such a receipt is read twice more:
  - the whole paper again at SECOND_PASS_WIDTH, keeping the texts found where the first reading found nothing;
  - the price column of every row that names something but has no amount, cut out and read alone.
"""

from __future__ import annotations

import re

from PIL import Image

from basketiq.domain.money import one_digit_apart, parse_money_cents
from basketiq.pipeline.layout import group_into_lines
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.parsers.base import MONEY
from basketiq.pipeline.preprocess import OcrTile, ocr_tiles
from basketiq.pipeline.schema import AMOUNT_TOKEN, TextToken

SECOND_PASS_WIDTH = 1400  # on the sample photos 1100 and 1200 found fewer of the missed prices
OVERLAP = 0.3  # of its area: a text this covered by one already read is the same text
PRICE_COLUMN = 0.55  # the prices sit right of this share of the paper width

_ALNUM_PAIR = re.compile(r"[^\W_]{2}")  # a lone "-" or "." read at another scale is noise
_NAME = re.compile(r"[A-Za-zÀ-ÿ]{3}")
_AMOUNT = re.compile(rf"{MONEY}(?!\d)")
_BROKEN_AMOUNT = re.compile(r"^[\d\s,.€eE-]*\d[\d\s,.€eE-]*$")  # "2,9 1": digits, no valid amount


def read_tiles(ocr: OcrEngine, tiles: list[OcrTile], page: int) -> list[TextToken]:
    """Every token of the slices, placed back on the page (a token belongs to the slice owning its centre)."""
    tokens: list[TextToken] = []
    for tile in tiles:
        for token in ocr.recognize(tile.image, page=page):
            centre = tile.top + (token.y0 + token.y1) / 2
            if tile.own_top <= centre < tile.own_bottom:
                tokens.append(token.model_copy(update={"y0": token.y0 + tile.top, "y1": token.y1 + tile.top}))
    return tokens


def read_small_print(
    ocr: OcrEngine, paper: Image.Image, width: int, tokens: list[TextToken], page: int
) -> list[TextToken]:
    """`tokens` (read on the paper scaled to `width` pixels) completed by the two extra looks."""
    enlarged = paper.resize(
        (SECOND_PASS_WIDTH, round(paper.height * SECOND_PASS_WIDTH / paper.width)), Image.Resampling.LANCZOS
    )
    second = ocr_tiles(enlarged)
    tokens = list(tokens)
    for token in read_tiles(ocr, second, page):
        moved = _scaled(token, width / second[0].image.width)
        if _ALNUM_PAIR.search(moved.text) and not any(_covers(found, moved) for found in tokens):
            tokens.append(moved)
    return _reread_prices(ocr, paper, width, tokens)


def _reread_prices(
    ocr: OcrEngine, paper: Image.Image, width: int, tokens: list[TextToken]
) -> list[TextToken]:
    """A row that names something and has no amount in the price column (nothing there, or digits that do not
    make an amount, "2,9 1") gets the band of its price column, at the height of the name, read as one line
    without the detector. A valid amount read there replaces what the first readings had in that place."""
    to_paper = paper.width / width
    column = PRICE_COLUMN * width
    dropped: set[int] = set()
    added: list[TextToken] = []
    for line in group_into_lines(tokens):
        names = [t for t in line.tokens if t.x0 < column and _NAME.search(t.text)]
        priced = [t for t in line.tokens if t.x0 >= column]
        if not names or _AMOUNT.search(line.text):  # an amount anywhere, even glued to the name
            continue
        if not all(_BROKEN_AMOUNT.match(t.text.strip()) for t in priced):
            continue  # words in the price column: a header or a footer line, not a price
        name = names[0]
        box = (round(column * to_paper), round(name.y0 * to_paper), paper.width, round(name.y1 * to_paper))
        if box[3] - box[1] < 2:
            continue
        text, confidence = ocr.read_line(paper.crop(box))
        amount = AMOUNT_TOKEN.match(text.strip())
        if not amount or _read_nearby(amount.group(), name, tokens):
            continue  # no price, or the price of the row above or below (a weighed line before its name)
        dropped.update(id(t) for t in priced)
        update = {"text": text.strip(), "x0": column, "x1": width, "confidence": confidence, "slope": None}
        added.append(name.model_copy(update=update))
    return [t for t in tokens if id(t) not in dropped] + added


def _read_nearby(amount: str, name: TextToken, tokens: list[TextToken]) -> bool:
    """Is `amount` already read within a row or so of `name` (or that one with a digit misread)?"""
    reach = 1.5 * (name.y1 - name.y0)
    centre = (name.y0 + name.y1) / 2
    cents = parse_money_cents(amount)
    if cents is None:
        return False
    return any(
        abs((t.y0 + t.y1) / 2 - centre) <= reach
        and (m := AMOUNT_TOKEN.match(t.text.strip()))
        and (read := parse_money_cents(m.group())) is not None
        and (read == cents or one_digit_apart(read, cents))
        for t in tokens
    )


def _scaled(token: TextToken, factor: float) -> TextToken:
    return token.model_copy(
        update={
            "x0": token.x0 * factor,
            "x1": token.x1 * factor,
            "y0": token.y0 * factor,
            "y1": token.y1 * factor,
        }
    )


def _covers(found: TextToken, new: TextToken) -> bool:
    """Does an already read text overlap a good part of `new` (the same text, read again)?"""
    width = min(found.x1, new.x1) - max(found.x0, new.x0)
    height = min(found.y1, new.y1) - max(found.y0, new.y0)
    area = (new.x1 - new.x0) * (new.y1 - new.y0)
    return width > 0 and height > 0 and width * height > OVERLAP * area
