from __future__ import annotations

from basketiq.app_config import AppConfig
from basketiq.config import Settings
from basketiq.pipeline.external import build_external
from basketiq.pipeline.llm import build_resolver
from basketiq.pipeline.ocr import build_ocr_engine
from basketiq.services.processing import Components


def build_components(settings: Settings, app_config: AppConfig) -> Components:
    """Processing components for one worker process. PaddleOCR models load lazily on first use."""
    return Components(
        ocr=build_ocr_engine(settings),
        resolver=build_resolver(app_config, settings),
        external=build_external(settings.external_fallback),
    )
