from __future__ import annotations

from basketiq.config import Settings
from basketiq.pipeline.ocr.base import NoOcr, OcrEngine, OcrUnavailableError


def build_ocr_engine(settings: Settings) -> OcrEngine:
    if settings.ocr_engine == "paddle":
        from basketiq.pipeline.ocr.paddle import PaddleOcrEngine

        return PaddleOcrEngine(settings.ocr_det_model, settings.ocr_rec_model)
    return NoOcr()


__all__ = ["NoOcr", "OcrEngine", "OcrUnavailableError", "build_ocr_engine"]
