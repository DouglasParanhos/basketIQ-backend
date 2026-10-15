from __future__ import annotations

from typing import Protocol

from PIL import Image

from basketiq.pipeline.schema import TextToken


class OcrUnavailableError(RuntimeError):
    """OCR engine is not installed/configured in this process."""


class OcrEngine(Protocol):
    name: str

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]: ...


class NoOcr:
    """Used by processes without OCR (e.g. API). Fails loudly instead of silently skipping."""

    name = "none"

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        raise OcrUnavailableError("OCR engine not available in this process")
