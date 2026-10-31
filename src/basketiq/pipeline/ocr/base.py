from __future__ import annotations

from typing import Protocol

from PIL import Image

from basketiq.pipeline.schema import TextToken


class OcrUnavailableError(RuntimeError):
    """OCR engine is not installed/configured in this process."""


class OcrEngine(Protocol):
    name: str

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]: ...

    def orientation(self, image: Image.Image) -> int:
        """Degrees (0, 90, 180, 270) to turn `image` counter-clockwise (`Image.rotate`) so its text reads
        upright."""
        ...

    def read_line(self, image: Image.Image) -> tuple[str, float]:
        """The text of an image holding one line of text, read without looking for text boxes first, and
        its confidence."""
        ...


class NoOcr:
    """Used by processes without OCR (e.g. API). Fails loudly instead of silently skipping."""

    name = "none"

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        raise OcrUnavailableError("OCR engine not available in this process")

    def orientation(self, image: Image.Image) -> int:
        raise OcrUnavailableError("OCR engine not available in this process")

    def read_line(self, image: Image.Image) -> tuple[str, float]:
        raise OcrUnavailableError("OCR engine not available in this process")
