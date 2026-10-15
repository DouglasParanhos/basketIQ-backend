from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from PIL import Image
from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas

from basketiq.domain.status import ProcessingRoute
from basketiq.pipeline.layout import group_into_lines
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


class FakeOcr:
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
