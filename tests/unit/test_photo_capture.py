"""A receipt photographed turned, at an angle, sideways, upside down or from far away is cut out, straightened
and turned upright before the OCR (RF-05); a receipt with few pixels gets extra looks at its prices."""

from __future__ import annotations

import math

import pytest
from PIL import Image, ImageDraw

from basketiq.pipeline import orchestrator, preprocess, recognition
from basketiq.pipeline.preprocess import crop_to_paper, flatten_paper, ocr_tiles, perspective_coefficients
from basketiq.pipeline.schema import TextToken
from tests.helpers import StubOcr, seen_at_an_angle

MAT = (25, 25, 25)


def _receipt(width: int = 300, height: int = 1500) -> Image.Image:
    """White paper with dark rows of "text", so the paper is broken by print like a real one."""
    paper = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(paper)
    for y in range(40, height - 40, 30):
        draw.rectangle((20, y, width - 20, y + 8), fill=(40, 40, 40))
    return paper


def _on_mat(paper: Image.Image, size: tuple[int, int] = (1200, 2000)) -> Image.Image:
    photo = Image.new("RGB", size, MAT)
    photo.paste(paper, ((size[0] - paper.width) // 2, (size[1] - paper.height) // 2))
    return photo


def _turned(degrees: float) -> Image.Image:
    return _on_mat(_receipt()).rotate(degrees, expand=False, fillcolor=MAT, resample=Image.Resampling.BICUBIC)


def _seen_at_an_angle() -> Image.Image:
    """The far end of the receipt narrower than the near one, as from a camera held low."""
    return seen_at_an_angle(_on_mat(_receipt()), 120, MAT)


def _upright_size_is_the_receipt(image: Image.Image) -> None:
    pad = 1 + 2 * preprocess.PAPER_PADDING
    assert 300 * 0.93 <= image.width <= 300 * pad * 1.04
    assert 1500 * 0.95 <= image.height <= 1500 * pad * 1.03


@pytest.mark.req("RF-05")
def test_perspective_coefficients_map_each_output_corner_to_its_source_corner() -> None:
    output = ((0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0))
    source = ((10.0, 5.0), (90.0, 15.0), (120.0, 210.0), (-5.0, 190.0))
    a, b, c, d, e, f, g, h = perspective_coefficients(output, source)
    for (x, y), (u, v) in zip(output, source, strict=True):
        w = g * x + h * y + 1
        assert math.isclose((a * x + b * y + c) / w, u, abs_tol=1e-6)
        assert math.isclose((d * x + e * y + f) / w, v, abs_tol=1e-6)


@pytest.mark.req("RF-05")
@pytest.mark.parametrize("degrees", [15, -15, 25])
def test_a_turned_receipt_is_cut_out_upright(degrees: float) -> None:
    _upright_size_is_the_receipt(flatten_paper(_turned(degrees)))


@pytest.mark.req("RF-05")
def test_a_receipt_seen_at_an_angle_is_straightened() -> None:
    _upright_size_is_the_receipt(flatten_paper(_seen_at_an_angle()))


@pytest.mark.req("RF-05")
def test_a_receipt_lying_sideways_is_stood_up() -> None:
    # Which end is up is the OCR engine's call (see the next tests): here only that it stands.
    _upright_size_is_the_receipt(flatten_paper(_on_mat(_receipt()).rotate(90, expand=True)))


@pytest.mark.req("RF-05")
def test_a_receipt_that_stands_straight_is_only_cropped() -> None:
    photo = _turned(1)  # within what layout.py undoes on its own
    assert flatten_paper(photo).size == crop_to_paper(photo).size


@pytest.mark.req("RF-05")
def test_a_receipt_far_away_is_still_cut_out() -> None:
    small = _receipt(150, 750)
    photo = _on_mat(small, (1600, 2400))  # the paper is 3 % of the photo
    paper = flatten_paper(photo)
    assert paper.width < 200 and paper.height < 850


@pytest.mark.req("RF-05")
@pytest.mark.parametrize("degrees", [12, -20])
def test_a_receipt_on_a_white_desk_is_levelled_by_its_text_and_cut_to_it(degrees: float) -> None:
    desk = (228, 224, 218)  # no dark table to tell the paper by
    photo = Image.new("RGB", (1400, 2200), desk)
    photo.paste(_receipt(), (550, 350))
    paper = flatten_paper(photo.rotate(degrees, fillcolor=desk, resample=Image.Resampling.BICUBIC))
    assert paper.width < 450 and 1350 <= paper.height <= 1700  # the rows run level again, the desk is gone


@pytest.mark.req("RF-05")
def test_a_screenshot_is_not_cut() -> None:
    page = _receipt(600, 1500)  # the text fills the image: nothing around it to cut away
    assert flatten_paper(page).size == page.size


@pytest.mark.req("RF-05")
def test_a_photo_without_a_clear_paper_is_left_alone() -> None:
    desk = Image.new("RGB", (900, 1600), "white")
    assert flatten_paper(desk).size == (900, 1600)


@pytest.mark.req("RF-05")
def test_a_sharp_wide_receipt_is_not_shrunk_to_the_narrow_slice_width() -> None:
    wide = ocr_tiles(Image.new("RGB", (1200, 5000), "white"))
    assert {t.image.width for t in wide} == {1200}
    huge = ocr_tiles(Image.new("RGB", (3000, 12000), "white"))
    assert {t.image.width for t in huge} == {preprocess.TILE_MAX_WIDTH}
    narrow = ocr_tiles(Image.new("RGB", (300, 2000), "white"))
    assert {t.image.width for t in narrow} == {preprocess.TILE_WIDTH}


class _TurnedOcr(StubOcr):
    """Says the page must be turned by `angle`."""

    def __init__(self, angle: int) -> None:
        self.angle = angle

    def orientation(self, image: Image.Image) -> int:
        return self.angle


@pytest.mark.req("RF-05")
def test_the_page_is_turned_by_the_angle_the_ocr_engine_gives() -> None:
    page = Image.new("RGB", (600, 800), "white")  # no paper to cut out: the page itself
    assert orchestrator._upright_paper(_TurnedOcr(90), page).size == (800, 600)
    assert orchestrator._upright_paper(_TurnedOcr(180), page).size == (600, 800)


class _PriceColumnOcr(StubOcr):
    """A band reader that finds `line`; the first readings found nothing more than the tokens given."""

    def __init__(self, line: str) -> None:
        self.line = line
        self.bands: list[tuple[int, int]] = []

    def read_line(self, image: Image.Image) -> tuple[str, float]:
        self.bands.append(image.size)
        return self.line, 0.9


def _name(text: str, y: float) -> TextToken:
    return TextToken(text=text, x0=50, y0=y, x1=400, y1=y + 30, confidence=0.9)


@pytest.mark.req("RF-05")
def test_a_small_receipt_gets_the_price_of_a_named_row_read_again() -> None:
    paper = Image.new("RGB", (240, 1600), "white")
    ocr = _PriceColumnOcr("0,99 €1")
    tokens = recognition.read_small_print(ocr, paper, 900, [_name("PAO PARA CACHORRO", 500)], 0)
    assert [t.text for t in tokens] == ["PAO PARA CACHORRO", "0,99 €1"]
    assert tokens[1].x0 >= recognition.PRICE_COLUMN * 900
    assert len(ocr.bands) == 1


@pytest.mark.req("RF-05")
def test_a_price_read_again_that_belongs_to_the_row_above_is_not_taken() -> None:
    paper = Image.new("RGB", (240, 1600), "white")
    above = [
        TextToken(text="0,298 kg x 1,99 €/kg", x0=120, y0=465, x1=400, y1=495, confidence=0.9),
        TextToken(text="0,59 € 1", x0=600, y0=465, x1=700, y1=495, confidence=0.9),
    ]
    tokens = recognition.read_small_print(
        _PriceColumnOcr("0,59€1"), paper, 900, [*above, _name("PEPINO", 500)], 0
    )
    assert [t.text for t in tokens] == ["0,298 kg x 1,99 €/kg", "0,59 € 1", "PEPINO"]


@pytest.mark.req("RF-05")
def test_a_header_or_footer_row_is_not_read_again() -> None:
    paper = Image.new("RGB", (240, 1600), "white")
    ocr = _PriceColumnOcr("1,00")
    footer = [_name("Contribuinte:", 500), TextToken(text="Unipessoal LDA", x0=600, y0=500, x1=880, y1=530)]
    recognition.read_small_print(ocr, paper, 900, footer, 0)
    assert ocr.bands == []
