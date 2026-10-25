"""Image normalisation for OCR. Works on copies; the stored original is never modified."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

TARGET_MIN_WIDTH = 900
MAX_SIDE = 4000

PAPER_PROBE_SIDE = 800  # the photo is shrunk to this before looking for the paper
PAPER_MIN_FILL = 0.5  # a column is paper when this share (of the best column) is bright
PAPER_MIN_ROW_FILL = 0.2  # a row inside the paper columns is paper when this share is bright
PAPER_PADDING = 0.02
PAPER_MIN_AREA, PAPER_MAX_AREA = 0.08, 0.95  # outside this share of the photo it is not a receipt crop

TILE_WIDTH = 900  # on the sample photos 1400 read no better and was slower
TILE_MAX_ASPECT = 2.5  # taller than this many widths: slice
TILE_HEIGHT_RATIO = 2.5  # slice height in widths; every slice costs about 20 s of CPU OCR
TILE_OVERLAP = 0.12
MAX_TILES = 12  # bounds the OCR time of a very long receipt


def load_image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as raw:
        upright = ImageOps.exif_transpose(raw)  # phone photos carry their rotation in EXIF
        return upright.convert("RGB")


def rasterize_pdf(data: bytes, dpi: int = 200) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(data)
    try:
        return [page.render(scale=dpi / 72).to_pil().convert("RGB") for page in pdf]
    finally:
        pdf.close()


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Upscale narrow receipts (screenshots are often ~300 px wide) and bound very large photos."""
    img = image
    width, height = img.size
    if width < TARGET_MIN_WIDTH:
        factor = TARGET_MIN_WIDTH / width
        new_h = int(height * factor)
        if new_h <= MAX_SIDE * 3:
            img = img.resize((TARGET_MIN_WIDTH, new_h), Image.Resampling.LANCZOS)
    elif max(width, height) > MAX_SIDE:
        img = img.copy()
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


@dataclass(frozen=True)
class OcrTile:
    """A slice of the receipt sent to the OCR on its own. `top` places the slice in the receipt, and a
    token is kept only when its centre lies in [own_top, own_bottom): neighbouring slices overlap so no
    text line is cut, and each line is read by exactly one slice."""

    image: Image.Image
    top: int
    own_top: float
    own_bottom: float


def crop_to_paper(image: Image.Image) -> Image.Image:
    """Crop a photographed receipt to the bright paper. A long receipt on a dark mat takes a third of
    the photo, and shrinking the whole photo for the OCR would shrink the text with it. When no clear
    paper is found (screenshot, white desk) the image is returned unchanged."""
    width, height = image.size
    probe = image
    scale = PAPER_PROBE_SIDE / max(width, height)
    if scale < 1:
        probe = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.BILINEAR
        )
    else:
        scale = 1.0
    gray = np.asarray(probe.convert("L"), dtype=np.uint8)
    bright = gray > _otsu_threshold(gray)
    fill = bright.mean(axis=0)
    cols = np.flatnonzero(fill > PAPER_MIN_FILL * fill.max())
    if cols.size == 0:
        return image
    left, right = int(cols[0]), int(cols[-1]) + 1
    rows = np.flatnonzero(bright[:, left:right].mean(axis=1) > PAPER_MIN_ROW_FILL)
    if rows.size == 0:
        return image
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    pad_x, pad_y = round(PAPER_PADDING * (right - left)), round(PAPER_PADDING * (bottom - top))
    box = (
        max(0, round((left - pad_x) / scale)),
        max(0, round((top - pad_y) / scale)),
        min(width, round((right + pad_x) / scale)),
        min(height, round((bottom + pad_y) / scale)),
    )
    area = (box[2] - box[0]) * (box[3] - box[1])
    if area < PAPER_MIN_AREA * width * height or area > PAPER_MAX_AREA * width * height:
        return image
    return image.crop(box)


def tile_spans(width: int, height: int) -> list[tuple[int, int, float, float]]:
    """(top, bottom, own_top, own_bottom) of the slices of a receipt `width` x `height` pixels. A receipt
    that is not much taller than wide is one slice."""
    if height <= width * TILE_MAX_ASPECT:
        return [(0, height, 0.0, float(height))]
    reach = 1 + (MAX_TILES - 1) * (1 - TILE_OVERLAP)  # MAX_TILES slices cover this many slice heights
    tile_height = max(round(width * TILE_HEIGHT_RATIO), math.ceil(height / reach))
    step = round(tile_height * (1 - TILE_OVERLAP))
    count = math.ceil((height - tile_height) / step) + 1
    tops = [min(i * step, height - tile_height) for i in range(count)]
    # A slice owns the rows up to the middle of the overlap with the next one.
    cuts = [(tops[i + 1] + tops[i] + tile_height) / 2 for i in range(count - 1)]
    owns = [0.0, *cuts], [*cuts, float(height)]
    return [(t, t + tile_height, owns[0][i], owns[1][i]) for i, t in enumerate(tops)]


def ocr_tiles(image: Image.Image) -> list[OcrTile]:
    """What to feed the OCR for one page: the paper cropped out of the photo and, when it is a long
    receipt, overlapping slices at a resolution where the text lines stay apart."""
    paper = crop_to_paper(image)
    width, height = paper.size
    if height <= width * TILE_MAX_ASPECT:
        return [OcrTile(prepare_for_ocr(paper), 0, 0.0, math.inf)]
    scale = TILE_WIDTH / width
    paper = paper.resize((TILE_WIDTH, round(height * scale)), Image.Resampling.LANCZOS)
    return [
        OcrTile(
            ImageOps.autocontrast(paper.crop((0, top, paper.width, bottom)), cutoff=1),
            top,
            own_top,
            own_bottom,
        )
        for top, bottom, own_top, own_bottom in tile_spans(paper.width, paper.height)
    ]


def _otsu_threshold(gray: np.ndarray) -> int:
    counts = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    levels = np.arange(256)
    weight = np.cumsum(counts)
    total = weight[-1]
    mean = np.cumsum(counts * levels)
    background, foreground = weight, total - weight
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean[-1] * background - mean * total) ** 2 / (background * foreground)
    return int(np.nanargmax(np.nan_to_num(between)))


def image_from_upload(data: bytes, mime: str) -> list[Image.Image]:
    if mime == "application/pdf":
        return rasterize_pdf(data)
    return [load_image(data)]
