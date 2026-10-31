from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from basketiq.domain.status import ProcessingRoute
from basketiq.pipeline.layout import group_into_lines
from basketiq.pipeline.preprocess import perspective_coefficients
from basketiq.pipeline.schema import DocumentText, TextToken

ROOT = Path(__file__).resolve().parent
SYNTHETIC = ROOT / "golden" / "synthetic"
FIXTURES = ROOT / "fixtures"


def doc_from_lines(
    lines: list[str], conf: float = 1.0, route: ProcessingRoute = ProcessingRoute.PDF_TEXT
) -> DocumentText:
    tokens = [
        TextToken(text=line, x0=0, y0=i * 10, x1=200, y1=i * 10 + 8, confidence=conf)
        for i, line in enumerate(lines)
        if line.strip()
    ]
    return DocumentText(lines=group_into_lines(tokens), route=route, engine="test")


def doc_from_cells(rows: list[list[tuple[str, float, float]]]) -> DocumentText:
    """A searchable PDF as the words with their x positions: each row is a list of (text, x0, x1)."""
    tokens = [
        TextToken(text=text, x0=x0, y0=i * 12, x1=x1, y1=i * 12 + 8)
        for i, row in enumerate(rows)
        for text, x0, x1 in row
    ]
    return DocumentText(lines=group_into_lines(tokens), route=ProcessingRoute.PDF_TEXT, engine="test")


class StubOcr:
    """An OCR engine that finds nothing: the pages stay as they are and no text is read. Fakes override what
    their test needs."""

    name = "stub-ocr"

    def orientation(self, image: Image.Image) -> int:
        return 0

    def read_line(self, image: Image.Image) -> tuple[str, float]:
        return "", 0.0

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        return []


class FakeOcr(StubOcr):
    """Deterministic OCR: returns the configured lines whatever the image is."""

    name = "fake-ocr"

    def __init__(self, lines: list[str], conf: float = 0.97, fail: Exception | None = None) -> None:
        self.lines = lines
        self.conf = conf
        self.fail = fail
        self.calls = 0

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        self.calls += 1
        if self.fail:
            raise self.fail
        return [
            TextToken(text=line, x0=0, y0=i * 20, x1=300, y1=i * 20 + 15, confidence=self.conf, page=page)
            for i, line in enumerate(self.lines)
        ]


class FakeResolver:
    name = "fake-llm"
    model_version = "fake|ambiguity-1"

    def __init__(self, proposals: Any = None, fail: Exception | None = None) -> None:
        self.proposals = proposals
        self.fail = fail
        self.calls: list[list[str]] = []

    def propose(self, merchant: str, lines: list[str]) -> Any:
        self.calls.append(lines)
        if self.fail:
            raise self.fail
        return self.proposals


class FakeExternal:
    def __init__(self, result: Any = None, fail: Exception | None = None, cost: int = 5) -> None:
        self.name = "fake-external"
        self.cost_per_call_cents = cost
        self.result = result
        self.fail = fail
        self.calls = 0

    def extract(self, data: bytes, mime: str, timeout: float) -> Any:
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.result


def make_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    height = (len(lines) * 5 + 30) * mm
    c = canvas.Canvas(buf, pagesize=(80 * mm, height))
    c.setFont("Courier", 8)
    y = height - 15 * mm
    for line in lines:
        c.drawString(5 * mm, y, line)
        y -= 5 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def photo_like(img: Image.Image) -> Image.Image:
    """Mild blur and sensor noise (seeded, so the photos are identical on every run)."""
    blurred = img.filter(ImageFilter.GaussianBlur(0.8))
    noise = np.random.default_rng(42).normal(0, 6, (blurred.height, blurred.width, 1))
    return Image.fromarray(np.clip(np.asarray(blurred) + noise, 0, 255).astype(np.uint8))


def seen_at_an_angle(img: Image.Image, inset: float, fill: tuple[int, ...]) -> Image.Image:
    """Photographed from below at an angle: the far (top) end `inset` pixels narrower on each side."""
    w, h = float(img.width), float(img.height)
    output = ((inset, 0.0), (w - inset, 0.0), (w, h), (0.0, h))
    source = ((0.0, 0.0), (w, 0.0), (w, h), (0.0, h))
    coefficients = perspective_coefficients(output, source)
    return img.transform(
        img.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC, fillcolor=fill
    )


def make_png(width: int = 600, height: int = 900, color: str | tuple[int, int, int] = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic(name: str) -> bytes:
    folder = SYNTHETIC / name
    return next(p for p in folder.iterdir() if p.name.startswith("document")).read_bytes()


PINGO_LINES = [
    "Pingo Doce - Distribuição Alimentar, S.A.",
    "NIPC: 500829993",
    "Artigos",
    "MERCEARIA",
    " C LEITE MEIO GORDO PD 1L 0,79",
    " C PAO DE FORMA 1,29",
    "      Poupança Imediata (0,30)",
    "TALHO",
    " C BIFE PERU 0,450 X 8,99 4,05",
    " E SUMO LARANJA 2 X 1,49 2,98",
    "Resumo",
    "TOTAL 9,11",
    "TOTAL POUPANÇA (0,30)",
    "TOTAL A PAGAR 8,81",
    "000001 2026-09-15 12:30 0001",
    "Data de emissão:15/09/2026",
]
PINGO_TOTAL = 881


# ------------------------------------------------------------------------------ fiscal QR code (A1)

ISSUER = "500829993"  # a chain's company number: public data
BUYER = "918273645"  # a made-up buyer number: it must never be stored or shown anywhere


def at_message(
    *,
    issuer: str = ISSUER,
    buyer: str = BUYER,
    day: str = "20260915",
    total: str = "8.81",
    tax: str = "0.50",
    number: str = "FS 04360642504231145/052961",
    atcud: str = "JJDBMXMX-052961",
    extra: str = "",
) -> str:
    """A fiscal QR message in the shape of the AT specification (point 4), with made-up values."""
    fields = [
        f"A:{issuer}", f"B:{buyer}", "C:PT", "D:FS", "E:N", f"F:{day}", f"G:{number}", f"H:{atcud}",
        "I1:PT", "I3:7.70", "I4:0.46", f"N:{tax}", f"O:{total}", "Q:AbCd", "R:1234",
    ]  # fmt: skip
    return "*".join(fields) + (f"*{extra}" if extra else "")


def qr_image(text: str, px: int = 240) -> Image.Image:
    """A QR code image, drawn by the library that reads it."""
    import zxingcpp

    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    modules = np.asarray(zxingcpp.write_barcode_to_image(barcode), dtype=np.uint8)
    return Image.fromarray(modules).convert("L").resize((px, px), Image.Resampling.NEAREST)


def make_pdf_with_qr(lines: list[str], qr_text: str | None, *, qr_page: int = 1, pages: int = 1) -> bytes:
    """A receipt PDF whose `qr_page`-th page carries the QR code (the text is on the first page)."""
    buf = io.BytesIO()
    height = (len(lines) * 5 + 90) * mm
    c = canvas.Canvas(buf, pagesize=(80 * mm, height))
    for page in range(1, pages + 1):
        c.setFont("Courier", 8)
        y = height - 15 * mm
        if page == 1:
            for line in lines:
                c.drawString(5 * mm, y, line)
                y -= 5 * mm
        if qr_text is not None and page == qr_page:
            image = io.BytesIO()
            qr_image(qr_text, 300).save(image, format="PNG")
            image.seek(0)
            c.drawImage(ImageReader(image), 20 * mm, 8 * mm, 40 * mm, 40 * mm)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_photo_with_qr(qr_text: str, size: tuple[int, int] = (900, 1600), qr_px: int = 130) -> bytes:
    """A photo-like image with a small QR code near the bottom."""
    photo = Image.new("RGB", size, (235, 235, 235))
    photo.paste(qr_image(qr_text, qr_px).convert("RGB"), ((size[0] - qr_px) // 2, size[1] - qr_px - 80))
    buf = io.BytesIO()
    photo.save(buf, format="PNG")
    return buf.getvalue()
