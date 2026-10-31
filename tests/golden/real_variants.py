"""The real receipts (GOLDEN_REAL_DIR, never committed) turned into the captures nobody sent us yet.

Each merchant only has some formats (Aldi/Auchan/Lidl/Mercadona: photos; Continente/Pingo Doce: PDFs), and
every real photo is a frontal, top-down shot. From each real document this builds:
  - `photographed`: a page (PDF, app screenshot) printed and photographed on a dark table (the photo route
    for PDF-only shops);
  - `rotated_15`, `perspective`, `sideways`, `upside_down`, `far`: that photo (or the real one) taken at an
    angle, sideways or upside down without EXIF, or from further away;
  - `white_desk`: the receipt alone on a white desk, seen at an angle and turned 12 degrees (no dark table
    to cut it out by);
  - `glare_fold`: the photo with a lamp's glare on part of the receipt and the shadow of a fold across it
    (both leave the print legible: text a glare wipes out is gone from any photo);
  - `scanned_pdf`: the capture inside an image-only PDF (the PDF route for photo-only shops);
  - `text_pdf`: for a shop with photos only, a searchable PDF with the text the OCR reads on the real photo,
    laid out in the receipt's own columns (an e-invoice that prints like the paper receipt).
A variant never shrinks the receipt's own pixels. A photo whose receipt is narrower than
`quality.MIN_PAPER_WIDTH` (one sent through a messaging app) must read right as it came; turned, warped or
scanned again it loses more detail to resampling, so there the contract is merchant, date and total, the
`low_resolution` warning on an image, and a review, not every item line. An e-invoice laid out differently
from the paper receipt stays unproven until a real one arrives (then: add it and calibrate the parser).
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from reportlab.pdfgen import canvas

from basketiq.domain.status import ReceiptStatus, SourceType
from basketiq.eval.golden import Case
from basketiq.pipeline import orchestrator
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.preprocess import (
    _otsu_threshold,
    crop_to_paper,
    flatten_paper,
    load_image,
    rasterize_pdf,
)
from basketiq.pipeline.quality import MIN_PAPER_WIDTH
from tests.helpers import photo_like, seen_at_an_angle

MAX_PIXELS = 48_000_000  # under validation.MAX_IMAGE_PIXELS
PRINT_MIN_WIDTH = 800
TABLE_GREY = 38
DESK = (228, 223, 216)


def _photograph_page(page: Image.Image) -> Image.Image:
    """A PDF page printed on slightly grey thermal paper, lying on a dark table, photographed."""
    paper = Image.fromarray((np.asarray(page, dtype=np.float32) * 0.93 + 6).astype(np.uint8))
    margin_x, margin_y = paper.width // 3, paper.height // 12
    table = np.random.default_rng(3).normal(
        TABLE_GREY, 8, (paper.height + 2 * margin_y, paper.width + 2 * margin_x, 1)
    )
    photo = Image.fromarray(np.clip(np.repeat(table, 3, axis=2), 0, 255).astype(np.uint8))
    photo.paste(paper, (margin_x, margin_y))
    return photo_like(photo)


def _border_colour(image: Image.Image) -> tuple[int, ...]:
    a = np.asarray(image)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return tuple(int(v) for v in np.median(border, axis=0))


def _rotated(degrees: float) -> Callable[[Image.Image], Image.Image]:
    def apply(image: Image.Image) -> Image.Image:
        return image.rotate(
            degrees, expand=True, fillcolor=_border_colour(image), resample=Image.Resampling.BICUBIC
        )

    return apply


def _perspective(image: Image.Image) -> Image.Image:
    """Taken from below the receipt at an angle: the far (top) end looks narrower, plus a small turn."""
    return _rotated(5)(seen_at_an_angle(image, 0.1 * image.width, _border_colour(image)))


def _far(image: Image.Image) -> Image.Image:
    """More of the table in the frame; the receipt keeps its own pixels."""
    factor = min(2.0, math.sqrt(MAX_PIXELS / (image.width * image.height)))
    size = (round(image.width * factor), round(image.height * factor))
    photo = Image.new("RGB", size, _border_colour(image))
    photo.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return photo


def _image_only_pdf(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=200.0)
    return buffer.getvalue()


# (capture, index of the case) -> photo; the turn alternates its direction from one case to the next
_TRANSFORMS: dict[str, Callable[[Image.Image, int], Image.Image]] = {
    "photographed": lambda image, _: image,
    "rotated_15": lambda image, index: _rotated(15 if index % 2 else -15)(image),
    "perspective": lambda image, _: _perspective(image),
    "sideways": lambda image, _: _rotated(90)(image),
    "upside_down": lambda image, _: _rotated(180)(image),
    "far": lambda image, _: _far(image),
    "glare_fold": lambda image, _: _glare_and_fold(image),
    "white_desk": lambda image, _: image,  # placed and turned on the desk by `_on_white_desk`
}
VARIANTS = (*_TRANSFORMS, "scanned_pdf", "text_pdf")


def _source(case: Case) -> tuple[Image.Image, bool]:
    """The real document as an image, and whether it is a page (a PDF, an app screenshot) rather than a
    photo with the table around the receipt."""
    data = case.document.read_bytes()
    if case.document.suffix.lower() == ".pdf":
        return rasterize_pdf(data)[0], True
    image = load_image(data)
    return image, crop_to_paper(image).size == image.size


def _printed(page: Image.Image) -> Image.Image:
    """A narrow screenshot printed at a phone photo's resolution (the OCR would upscale it the same way)."""
    if page.width >= PRINT_MIN_WIDTH:
        return page
    size = (PRINT_MIN_WIDTH, round(page.height * PRINT_MIN_WIDTH / page.width))
    return page.resize(size, Image.Resampling.LANCZOS)


def _capture(image: Image.Image, is_page: bool) -> Image.Image:
    """What a phone camera sees: the real photo (EXIF applied), or the page printed and photographed."""
    return _photograph_page(_printed(image)) if is_page else image


def _paper_alone(image: Image.Image, is_page: bool) -> Image.Image:
    """The receipt without what it lay on: the page, or the paper of the photo with the table left beside a
    curled edge painted the colour of the desk."""
    if is_page:
        return _printed(image)
    paper = crop_to_paper(image)
    gray = np.asarray(paper.convert("L"))
    closed = Image.fromarray(gray).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    bright = np.asarray(closed) > _otsu_threshold(gray)
    pixels = np.asarray(paper).copy()
    for y, row in enumerate(bright):
        xs = np.flatnonzero(row)
        if xs.size == 0:
            pixels[y] = DESK
        else:
            pixels[y, : xs[0]] = DESK
            pixels[y, xs[-1] + 1 :] = DESK
    return Image.fromarray(pixels)


def _on_white_desk(paper: Image.Image, index: int) -> Image.Image:
    margin_x, margin_y = paper.width // 3, paper.height // 12
    shape = (paper.height + 2 * margin_y, paper.width + 2 * margin_x, 1)
    desk = np.random.default_rng(5).normal(1.0, 0.025, shape) * np.array(DESK)
    photo = Image.fromarray(np.clip(desk, 0, 255).astype(np.uint8))
    photo.paste(paper, (margin_x, margin_y))
    photo = seen_at_an_angle(photo, 0.08 * photo.width, DESK)
    degrees = 12 if index % 2 else -12
    return photo.rotate(degrees, expand=True, fillcolor=DESK, resample=Image.Resampling.BICUBIC)


def _glare_and_fold(image: Image.Image) -> Image.Image:
    """A lamp's glare (the paper washed towards white around a point, the print fainter but legible) and the
    shadow of a fold across the receipt."""
    h, w = image.height, image.width
    y, x = np.ogrid[0:h, 0:w]  # broadcast, not two full-size grids
    glare = np.exp(-(((x - 0.6 * w) / (0.35 * w)) ** 2 + ((y - 0.3 * h) / (0.15 * h)) ** 2))[..., None]
    pixels = np.asarray(image, dtype=np.float32)
    pixels = pixels + (255 - pixels) * 0.55 * glare
    from_fold = np.abs(y - (0.55 * h + 0.08 * (x - w / 2)))
    pixels = pixels * np.where(from_fold < max(12, h // 80), 0.85, 1.0)[..., None]
    pixels[from_fold < max(2, h // 400)] *= 0.6
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def _text_pdf(photo: bytes, engine: OcrEngine) -> bytes:
    """The words the OCR reads on the photo, each at its column (never overlapping the previous word, or the
    PDF reader would glue them), one printed row per text line."""
    outcome = orchestrator.PipelineOutcome(status=ReceiptStatus.EXTRACTING)
    lines = orchestrator._extract_text(photo, SourceType.IMAGE, engine, outcome).lines
    tokens = [t for line in lines for t in line.tokens]
    left, right = min(t.x0 for t in tokens), max(t.x1 for t in tokens)
    page_width, margin, pitch, size = 227.0, 12.0, 10.0, 6.0  # an 80 mm receipt in Courier
    scale = (page_width - 2 * margin) / (right - left)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_width, pitch * (len(lines) + 4)))
    pdf.setFont("Courier", size)
    for row, line in enumerate(lines):
        y, end = pitch * (len(lines) + 2 - row), 0.0
        for token in line.tokens:
            text = token.text.strip()
            x = max(margin + (token.x0 - left) * scale, end + size)
            pdf.drawString(x, y, text)
            end = x + 0.6 * size * len(text)
    pdf.save()
    return buffer.getvalue()


def build(case: Case, variant: str, index: int, engine: OcrEngine) -> tuple[bytes, str, dict] | None:
    """(document bytes, file suffix, expected) of one variant of a real case, or None when it does not
    apply (a photo is not photographed again, a PDF needs no text PDF)."""
    expected = {k: v for k, v in case.expected.items() if k not in ("status", "review_codes")}
    if variant == "text_pdf":
        if case.document.suffix.lower() == ".pdf":
            return None
        expected.update(route="PDF_TEXT", requires_ocr=False)
        return _text_pdf(case.document.read_bytes(), engine), ".pdf", expected
    source, is_page = _source(case)
    if variant == "photographed" and not is_page:
        return None
    expected.update(route="LOCAL_OCR", requires_ocr=True)
    if not is_page and flatten_paper(source).width < MIN_PAPER_WIDTH:
        expected.pop("lines", None)
        expected["status"] = "REVIEW_REQUIRED"
        if variant != "scanned_pdf":  # the quality checks look at photos, not at the pages of a PDF
            expected["quality_warnings"] = ["low_resolution"]
    if variant == "white_desk":
        capture = _on_white_desk(_paper_alone(source, is_page), index)
    else:
        capture = _capture(source, is_page)
    if variant == "scanned_pdf":
        return _image_only_pdf(capture), ".pdf", expected
    buffer = io.BytesIO()
    _TRANSFORMS[variant](capture, index).save(
        buffer, "JPEG", quality=92
    )  # a fresh JPEG carries no EXIF orientation
    return buffer.getvalue(), ".jpg", expected


def write_variant(cases: list[Case], variant: str, folder: Path, engine: OcrEngine) -> list[str]:
    """Every real case as `variant`, laid out like GOLDEN_REAL_DIR (`<merchant>/<name>.<ext>` + expected)."""
    written = []
    for index, case in enumerate(cases):
        built = build(case, variant, index, engine)
        if built is None:
            continue
        data, suffix, expected = built
        target = folder / case.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_name(target.name + suffix).write_bytes(data)
        target.with_name(target.name + ".expected.json").write_text(json.dumps(expected), encoding="utf-8")
        written.append(case.name)
    return written
