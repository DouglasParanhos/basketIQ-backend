from __future__ import annotations

import pytest

from basketiq.pipeline.ocr import OcrEngine


@pytest.fixture(scope="session")
def paddle_engine() -> OcrEngine:
    """One local PaddleOCR engine for the whole session, so its models load once."""
    from basketiq.config import get_settings
    from basketiq.pipeline.ocr.paddle import PaddleOcrEngine

    settings = get_settings()
    return PaddleOcrEngine(settings.ocr_det_model, settings.ocr_rec_model, settings.ocr_enable_mkldnn)
