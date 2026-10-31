"""Photos of receipts that are tilted or curled must read as well as flat ones (plano de testes §6).

A synthetic receipt of every merchant is rendered like a thermal receipt (descriptions on the left,
amounts right-aligned), distorted like a hand-held photo and read with the local OCR. Needs the
PaddleOCR models (marker `ocr`, like the other OCR cases); 28 reads, so it adds about two minutes. Turned,
upside down and at an angle they lie on a dark table, as a receipt photographed on white is not cut out.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from basketiq.eval.golden import run
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.pdf_text import extract_pdf_text
from tests.helpers import photo_like, seen_at_an_angle

SYNTHETIC = Path(__file__).resolve().parent / "synthetic"
MERCHANT_CASES = ["pingo_doce_text", "lidl_text", "continente_text", "auchan_text"]

WIDTH, PITCH, MARGIN = 1000, 44, 50
_ROW = re.compile(
    r"^(?P<left>.*[A-Za-zÀ-ÿ].*?)\s+(?P<right>-?\d{1,4},\d{2}(?:\s+\d{1,4},\d{2})*(?:\s+[A-Z])?)$"
)

Distortion = Callable[[Image.Image], Image.Image]


def render_receipt(lines: list[str]) -> Image.Image:
    """Thermal-receipt look: text on the left, the amounts of a row right-aligned."""
    font = ImageFont.load_default(size=26)
    img = Image.new("RGB", (WIDTH, PITCH * len(lines) + 2 * MARGIN), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        y = MARGIN + i * PITCH
        row = _ROW.match(line)
        draw.text((MARGIN, y), row["left"] if row else line, fill="black", font=font)
        if row:
            draw.text((WIDTH - MARGIN, y), row["right"], fill="black", font=font, anchor="ra")
    return img


def rotated(degrees: float) -> Distortion:
    return lambda img: img.rotate(degrees, expand=True, fillcolor="white", resample=Image.Resampling.BICUBIC)


def curled(top_slope: float, bottom_slope: float) -> Distortion:
    """Paper curl: rows are tilted by `top_slope` (dy/dx) near the top and `bottom_slope` near the bottom."""

    def apply(img: Image.Image) -> Image.Image:
        array = np.asarray(img)
        height, width = array.shape[:2]
        slope = np.linspace(top_slope, bottom_slope, height)[:, None]
        source_y = np.arange(height)[:, None] + slope * (np.arange(width)[None, :] - width / 2)
        rows = np.clip(np.rint(source_y).astype(int), 0, height - 1)
        return Image.fromarray(array[rows, np.arange(width)[None, :]])

    return apply


TABLE = (40, 36, 34)


def on_table(img: Image.Image) -> Image.Image:
    """The receipt lying on a dark table, with room around it to be turned."""
    margin = img.height // 4
    photo = Image.new("RGB", (img.width + 2 * margin, img.height + 2 * margin), TABLE)
    photo.paste(img, (margin, margin))
    return photo


def turned(degrees: float) -> Distortion:
    return lambda img: on_table(img).rotate(degrees, fillcolor=TABLE, resample=Image.Resampling.BICUBIC)


def at_an_angle(img: Image.Image) -> Image.Image:
    """Taken from below at an angle: the far end narrower than the near one."""
    photo = on_table(img)
    return seen_at_an_angle(photo, 0.12 * photo.width, TABLE)


DISTORTIONS: dict[str, Distortion] = {
    "flat": lambda img: img,
    "rotated_left": rotated(2.0),
    "rotated_right": rotated(-3.0),
    "curled": curled(-0.05, 0.04),
    "turned_15": turned(15),
    "upside_down": turned(180),
    "at_an_angle": at_an_angle,
}


def write_case(case: str, folder: Path, distort: Distortion) -> None:
    source = SYNTHETIC / case
    lines = [line.text for line in extract_pdf_text((source / "document.pdf").read_bytes()).lines]
    target = folder / case
    target.mkdir(parents=True)
    photo_like(distort(render_receipt(lines))).save(target / "document.png")
    expected = json.loads((source / "expected.json").read_text(encoding="utf-8"))
    expected.update(route="LOCAL_OCR", requires_ocr=True)
    expected.pop("status", None)  # a photo may legitimately ask for a review
    (target / "expected.json").write_text(json.dumps(expected), encoding="utf-8")


@pytest.mark.ocr
@pytest.mark.req("PT-6")
@pytest.mark.parametrize("distort", DISTORTIONS.values(), ids=list(DISTORTIONS))
def test_distorted_photos_of_every_merchant_read_like_flat_ones(
    distort: Distortion, tmp_path: Path, paddle_engine: OcrEngine
) -> None:
    for case in MERCHANT_CASES:
        write_case(case, tmp_path, distort)
    report = run([tmp_path], use_ocr=True, engine=paddle_engine)
    failures = {m: s["failures"] for m, s in report["per_merchant"].items() if s["failures"]}
    assert failures == {}
