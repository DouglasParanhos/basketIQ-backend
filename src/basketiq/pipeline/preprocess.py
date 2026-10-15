"""Image normalisation for OCR. Works on copies; the stored original is never modified."""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

TARGET_MIN_WIDTH = 900
MAX_SIDE = 4000


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


def image_from_upload(data: bytes, mime: str) -> list[Image.Image]:
    if mime == "application/pdf":
        return rasterize_pdf(data)
    return [load_image(data)]
