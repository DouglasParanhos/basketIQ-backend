"""Image normalisation for OCR. Works on copies; the stored original is never modified."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

TARGET_MIN_WIDTH = 900
MAX_SIDE = 4000

PAPER_PROBE_SIDE = 800  # the photo is shrunk to this before looking for the paper
PAPER_MIN_FILL = 0.5  # a column is paper when this share (of the best column) is bright
PAPER_MIN_ROW_FILL = 0.2  # a row inside the paper columns is paper when this share is bright
PAPER_PADDING = 0.02
PAPER_MIN_AREA, PAPER_MAX_AREA = 0.02, 0.95  # outside this share of the photo it is not a receipt crop
PAPER_MIN_EDGE_ROWS = 20  # probe rows needed to trace the long edges of the paper
PAPER_MAX_EDGE_MISS = 0.08  # of the paper width: edge points this far off a straight line are no clean edge
PAPER_STRAIGHT = 0.05  # edges within ~3 degrees of the image axes: crop (layout.py undoes such a tilt)
TEXT_MAX_TILT = 30  # degrees: the turns a photo without a clear paper is searched for
TEXT_STRAIGHT = 3.0  # degrees: text turned less than this is left to layout.py
TEXT_MARGIN = 0.05  # of the text's width and height, kept around it
TEXT_CROP_MAX = 0.6  # of the photo: text filling more of it (a screenshot) is not cut out
TEXT_MAX_INK = 0.5  # of the photo: more 'ink' than this is no text on paper
TEXT_DESK_GAP = 15  # grey levels: marks this close to the desk's own grey are not text
TEXT_MIN_INK = 0.003  # of a row (column): less ink than this is a speck on the desk, not text
PAPER_TEXT_GAP = 7  # probe pixels: dark gaps (printed text) up to this wide do not split the paper

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]

TILE_WIDTH = 900  # a narrower receipt is enlarged to this before it is sliced
TILE_MAX_WIDTH = 1400  # a wider one keeps its pixels up to this: shrinking a sharp photo misreads digits
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


def _gray_probe(image: Image.Image) -> tuple[np.ndarray, float]:
    """The photo shrunk to PAPER_PROBE_SIDE, in grey, and the shrink factor."""
    width, height = image.size
    probe = image
    scale = PAPER_PROBE_SIDE / max(width, height)
    if scale < 1:
        probe = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.BILINEAR
        )
    else:
        scale = 1.0
    return np.asarray(probe.convert("L"), dtype=np.uint8), scale


def _paper_box(bright: np.ndarray) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the paper in the probe, or None when there is no clear paper."""
    fill = bright.mean(axis=0)
    cols = np.flatnonzero(fill > PAPER_MIN_FILL * fill.max())
    if cols.size == 0:
        return None
    left, right = int(cols[0]), int(cols[-1]) + 1
    rows = np.flatnonzero(bright[:, left:right].mean(axis=1) > PAPER_MIN_ROW_FILL)
    if rows.size == 0:
        return None
    return left, int(rows[0]), right, int(rows[-1]) + 1


def _plausible_area(area: float, image: Image.Image) -> bool:
    return PAPER_MIN_AREA * image.width * image.height <= area <= PAPER_MAX_AREA * image.width * image.height


def crop_to_paper(image: Image.Image) -> Image.Image:
    """Crop a photographed receipt to the bright paper, without straightening it (what `flatten_paper` does
    for a receipt that stands straight). When no clear paper is found the image is returned unchanged."""
    gray, scale = _gray_probe(image)
    found = _paper_box(gray > _otsu_threshold(gray))
    return (_crop(image, found, scale) if found else None) or image


def _crop(image: Image.Image, found: tuple[int, int, int, int], scale: float) -> Image.Image | None:
    """The paper `found` in the probe (shrunk by `scale`) cut out of `image` with a margin, or None when that
    is not a plausible share of the photo."""
    left, top, right, bottom = found
    pad_x, pad_y = round(PAPER_PADDING * (right - left)), round(PAPER_PADDING * (bottom - top))
    box = (
        max(0, round((left - pad_x) / scale)),
        max(0, round((top - pad_y) / scale)),
        min(image.width, round((right + pad_x) / scale)),
        min(image.height, round((bottom + pad_y) / scale)),
    )
    return image.crop(box) if _plausible_area((box[2] - box[0]) * (box[3] - box[1]), image) else None


def _edge_fit(ys: np.ndarray, xs: np.ndarray) -> tuple[float, float, float]:
    """x = slope * y + offset through the edge points, refitted without the points far from the line (a
    torn corner, a shadow); also the median distance of the kept points."""
    keep = np.ones(ys.size, bool)
    for _ in range(3):
        slope, offset = np.polyfit(ys[keep], xs[keep], 1)
        residual = np.abs(xs - (slope * ys + offset))
        keep = residual <= max(2.0, 2.5 * float(np.median(residual[keep])))
    return float(slope), float(offset), float(np.median(residual[keep]))


def _paper_quad(bright: np.ndarray, box: tuple[int, int, int, int]) -> Quad | None:
    """Corners (top-left, top-right, bottom-right, bottom-left) of a standing receipt in the probe.

    Each row crosses the paper once: its longest bright run gives a point of the left and of the right
    edge, and a line through each set gives the two long edges, however the receipt is turned or seen in
    perspective. The two ends are where the paper stops along the direction of those edges."""
    # The box only holds the columns most rows cross: a turned receipt reaches beyond it, so the whole probe
    # is traced, keeping in each row the longest run that touches the box.
    left, _, right, _ = box
    ys, starts, ends = [], [], []
    for row_index, row in enumerate(bright):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        run_starts, run_ends = edges[::2], edges[1::2]
        touching = (run_ends > left) & (run_starts < right)
        if touching.any():
            run = int(np.argmax(np.where(touching, run_ends - run_starts, -1)))
            ys.append(row_index)
            starts.append(run_starts[run])
            ends.append(run_ends[run])
    y, x0, x1 = np.array(ys, float), np.array(starts, float), np.array(ends, float)
    wide = (x1 - x0) >= 0.5 * np.median(x1 - x0)  # rows that cut the paper end-on are no edge
    y, x0, x1 = y[wide], x0[wide], x1[wide]
    if y.size < PAPER_MIN_EDGE_ROWS:
        return None
    left_slope, left_offset, left_miss = _edge_fit(y, x0)
    right_slope, right_offset, right_miss = _edge_fit(y, x1)
    paper_width = float(np.median(x1 - x0))
    if max(left_miss, right_miss) > PAPER_MAX_EDGE_MISS * paper_width:
        return None
    # The ends: the paper pixels between the two edges, projected on the direction of the edges.
    slope = (left_slope + right_slope) / 2
    norm = math.hypot(slope, 1.0)
    ux, uy = slope / norm, 1 / norm
    py, px = np.nonzero(bright)
    inside = (px >= left_slope * py + left_offset - 2) & (px <= right_slope * py + right_offset + 2)
    along = px[inside] * ux + py[inside] * uy
    if along.size == 0:
        return None
    first, last = np.percentile(along, [0.2, 99.8])

    def corner(edge_slope: float, edge_offset: float, at: float) -> tuple[float, float]:
        cy = (at - edge_offset * ux) / (edge_slope * ux + uy)
        return edge_slope * cy + edge_offset, cy

    return (
        corner(left_slope, left_offset, first),
        corner(right_slope, right_offset, first),
        corner(right_slope, right_offset, last),
        corner(left_slope, left_offset, last),
    )


def flatten_paper(image: Image.Image) -> Image.Image:
    """The receipt cut out of the photo, standing upright as a flat rectangle (or upside down: telling
    which is the OCR engine's job). A receipt photographed turned, at an angle or lying sideways is turned
    and straightened here, so its rows run level for the OCR. A receipt that already stands straight is
    cropped exactly like `crop_to_paper`. Without a clear paper (a receipt on a white desk, a screenshot) the
    photo is only turned so that its text runs level."""
    gray, scale = _gray_probe(image)
    threshold = _otsu_threshold(gray)
    box = _paper_box(gray > threshold)
    if box is None or not _plausible_area((box[2] - box[0]) * (box[3] - box[1]) / scale**2, image):
        return _level_text(image, gray, threshold)
    left, top, right, bottom = box
    if right - left > bottom - top:  # paper lying sideways: stand it up first
        return flatten_paper(image.rotate(90, expand=True))
    # The printed text breaks the paper into short bright runs: close those gaps before tracing the edges.
    closed = (
        Image.fromarray(gray)
        .filter(ImageFilter.MaxFilter(PAPER_TEXT_GAP))
        .filter(ImageFilter.MinFilter(PAPER_TEXT_GAP))
    )
    quad = _paper_quad(np.asarray(closed) > threshold, box)
    if quad is None:
        return _crop(image, box, scale) or _level_text(image, gray, threshold)
    tl, tr, br, bl = corners = np.array(quad) / scale
    width = float(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = float(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    turned = max(abs(tr[1] - tl[1]) / width, abs(bl[0] - tl[0]) / height, abs(br[0] - tr[0]) / height)
    if turned < PAPER_STRAIGHT or not _plausible_area(width * height, image):
        return _crop(image, box, scale) or _level_text(image, gray, threshold)
    pad_x, pad_y = PAPER_PADDING * width, PAPER_PADDING * height
    size = (round(width + 2 * pad_x), round(height + 2 * pad_y))
    output = (
        (pad_x, pad_y),
        (pad_x + width, pad_y),
        (pad_x + width, pad_y + height),
        (pad_x, pad_y + height),
    )
    return image.transform(
        size, Image.Transform.PERSPECTIVE, perspective_coefficients(output, corners), Image.Resampling.BICUBIC
    )


def _level_text(image: Image.Image, gray: np.ndarray, threshold: int) -> Image.Image:
    """The photo turned so that its rows of text run level, when they are turned more than layout.py undoes
    (the angle at which the dark pixels pile up into the sharpest rows: a projection profile), and cut to
    where the text is, so a small receipt on a large desk is not read as the whole photo."""
    ink = gray <= threshold
    if not 0 < ink.mean() < TEXT_MAX_INK:  # a blank page: the threshold split nothing that could be text
        return image
    dark = Image.fromarray((ink * 255).astype(np.uint8))

    def sharpness(angle: float) -> float:
        rows = np.asarray(dark.rotate(angle, resample=Image.Resampling.BILINEAR)).sum(
            axis=1, dtype=np.float64
        )
        return float(np.sum(np.diff(rows) ** 2))  # sharp changes: text lines and the gaps between them

    coarse = max(np.arange(-TEXT_MAX_TILT, TEXT_MAX_TILT + 1, 1.0), key=sharpness)
    angle = float(max(np.arange(coarse - 1, coarse + 1.01, 0.25), key=sharpness))
    # Small print shrunk into the probe turns grey: the text is bounded by anything darker than half-way
    # between the ink and white, but lighter marks than the desk are not text.
    desk = int(np.median(np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])))
    faint = Image.fromarray(
        ((gray <= min((threshold + 255) // 2, desk - TEXT_DESK_GAP)) * 255).astype(np.uint8)
    )
    if abs(angle) >= TEXT_STRAIGHT:
        image = image.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(desk,) * 3)
        faint = faint.rotate(angle, expand=True)
    text = np.asarray(faint) > 127
    # Rows and columns with some ink in them: a lone speck on the desk is not text.
    rows = np.flatnonzero(text.sum(axis=1) >= max(2, TEXT_MIN_INK * text.shape[1]))
    cols = np.flatnonzero(text.sum(axis=0) >= max(2, TEXT_MIN_INK * text.shape[0]))
    if rows.size == 0 or cols.size == 0:
        return image
    left, right, top, bottom = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    to_image = image.width / faint.width
    pad_x, pad_y = TEXT_MARGIN * (right - left), TEXT_MARGIN * (bottom - top)
    box = (
        max(0, round((left - pad_x) * to_image)),
        max(0, round((top - pad_y) * to_image)),
        min(image.width, round((right + pad_x) * to_image)),
        min(image.height, round((bottom + pad_y) * to_image)),
    )
    area = (box[2] - box[0]) * (box[3] - box[1])
    return image.crop(box) if 0 < area <= TEXT_CROP_MAX * image.width * image.height else image


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


def ocr_tiles(paper: Image.Image) -> list[OcrTile]:
    """What to feed the OCR for the paper of one page (see `flatten_paper`): the whole of it or, when it is
    a long receipt, overlapping slices at a resolution where the text lines stay apart."""
    width, height = paper.size
    if height <= width * TILE_MAX_ASPECT:
        return [OcrTile(prepare_for_ocr(paper), 0, 0.0, math.inf)]
    target = min(max(width, TILE_WIDTH), TILE_MAX_WIDTH)
    if target != width:
        paper = paper.resize((target, round(height * target / width)), Image.Resampling.LANCZOS)
    return [
        OcrTile(
            ImageOps.autocontrast(paper.crop((0, top, paper.width, bottom)), cutoff=1),
            top,
            own_top,
            own_bottom,
        )
        for top, bottom, own_top, own_bottom in tile_spans(paper.width, paper.height)
    ]


def perspective_coefficients(output: Quad, source: Quad | np.ndarray) -> tuple[float, ...]:
    """The 8 coefficients `Image.transform(..., PERSPECTIVE, ...)` needs so that each corner of `output`
    shows the matching corner of `source` (PIL maps output pixels back to source pixels)."""
    rows, values = [], []
    for (x, y), (u, v) in zip(output, source, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values += [u, v]
    return tuple(float(c) for c in np.linalg.solve(np.array(rows, float), np.array(values, float)))


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
