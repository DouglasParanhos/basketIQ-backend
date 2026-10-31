from __future__ import annotations

import io
import math
from itertools import pairwise

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas

from basketiq.pipeline import orchestrator, preprocess
from basketiq.pipeline.pdf_text import extract_pdf_text
from basketiq.pipeline.preprocess import OcrTile, crop_to_paper, flatten_paper, ocr_tiles, tile_spans
from basketiq.pipeline.schema import TextToken
from tests.helpers import StubOcr


def _photo(paper: tuple[int, int, int, int] | None, size: tuple[int, int] = (900, 1600)) -> Image.Image:
    """A dark mat with a white strip of paper (or just a white desk when `paper` is None)."""
    image = Image.new("RGB", size, "white" if paper is None else (25, 25, 25))
    if paper:
        ImageDraw.Draw(image).rectangle(paper, fill="white")
    return image


@pytest.mark.req("RF-05")
def test_crop_to_paper_keeps_the_receipt_and_drops_the_mat() -> None:
    cropped = crop_to_paper(_photo((350, 20, 580, 1500)))
    assert 230 <= cropped.width <= 260
    assert 1480 <= cropped.height <= 1580


@pytest.mark.req("RF-05")
def test_crop_to_paper_leaves_a_photo_without_clear_paper_alone() -> None:
    assert crop_to_paper(_photo(None)).size == (900, 1600)


@pytest.mark.req("RF-05")
def test_a_receipt_that_is_not_tall_is_one_slice() -> None:
    assert tile_spans(900, 2000) == [(0, 2000, 0.0, 2000.0)]


@pytest.mark.req("RF-05")
@pytest.mark.parametrize("height", [2300, 5400, 40000])
def test_slices_overlap_and_each_row_belongs_to_exactly_one(height: int) -> None:
    spans = tile_spans(900, height)
    assert 2 <= len(spans) <= preprocess.MAX_TILES
    assert spans[0][0] == 0 and spans[-1][1] == height
    assert spans[0][2] == 0 and spans[-1][3] == height
    for (_, bottom, _, own_bottom), (top, _, own_top, _) in pairwise(spans):
        assert top < bottom  # the slices overlap
        assert own_bottom == own_top  # and the rows are shared out without gaps or repeats
        assert top < own_top < bottom


@pytest.mark.req("RF-05")
def test_a_tall_photo_is_cropped_then_sliced_at_the_slice_width() -> None:
    tiles = ocr_tiles(flatten_paper(_photo((350, 20, 580, 1590))))
    assert len(tiles) >= 2
    assert {t.image.width for t in tiles} == {preprocess.TILE_WIDTH}
    assert math.isinf(OcrTile(tiles[0].image, 0, 0.0, math.inf).own_bottom)


class _RowsOcr(StubOcr):
    """Reads a text at every 100th row of the receipt, whatever slice it is handed."""

    name = "rows"

    def __init__(self, tops: list[int]) -> None:
        self.tops = tops
        self.calls = 0

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        top = self.tops[self.calls]
        self.calls += 1
        return [
            TextToken(text=f"row{y}", x0=0, y0=y - top - 10, x1=50, y1=y - top + 10, page=page)
            for y in range(50, 1000, 100)
            if 0 <= y - top <= image.height
        ]


@pytest.mark.req("RF-05")
def test_overlapping_slices_give_every_text_once_at_its_place_on_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blank = Image.new("RGB", (900, 600))
    tiles = [OcrTile(blank, 0, 0.0, 450.0), OcrTile(blank, 400, 450.0, math.inf)]
    monkeypatch.setattr(orchestrator, "ocr_tiles", lambda image: tiles)
    tokens = orchestrator._recognize_page(_RowsOcr([0, 400]), blank, page=0)
    assert [t.text for t in sorted(tokens, key=lambda t: t.y0)] == [f"row{y}" for y in range(50, 1000, 100)]
    assert all(t.y0 == int(t.text[3:]) - 10 for t in tokens)


@pytest.mark.req("RF-05")
def test_transparent_screenshot_background_is_white_not_black() -> None:
    shot = Image.new("RGBA", (40, 40), (0, 0, 0, 0))  # transparent around the receipt
    ImageDraw.Draw(shot).rectangle((10, 10, 29, 29), fill=(20, 20, 20, 255))
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    image = preprocess.load_image(buf.getvalue())
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((20, 20)) == (20, 20, 20)


@pytest.mark.req("F-26")
def test_bold_text_drawn_twice_is_read_once() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(80 * mm, 40 * mm))
    c.setFont("Courier", 8)
    for _ in range(2):  # how a Lidl e-invoice prints bold: the same characters at the same place
        c.drawString(5 * mm, 20 * mm, "0,464 kg x 6,19 EUR/kg")
    c.save()
    assert [line.text for line in extract_pdf_text(buf.getvalue()).lines] == ["0,464 kg x 6,19 EUR/kg"]
