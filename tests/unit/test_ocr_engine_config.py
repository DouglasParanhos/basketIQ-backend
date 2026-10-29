"""oneDNN is on by default; BASKETIQ_OCR_ENABLE_MKLDNN=false turns it off (hosts lacking the CPU support)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from basketiq.config import Settings
from basketiq.pipeline.ocr import build_ocr_engine
from basketiq.pipeline.ocr.paddle import PaddleOcrEngine


def test_mkldnn_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASKETIQ_OCR_ENABLE_MKLDNN", raising=False)
    assert Settings().ocr_enable_mkldnn is True


def test_env_var_turns_mkldnn_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASKETIQ_OCR_ENABLE_MKLDNN", "false")
    assert Settings().ocr_enable_mkldnn is False


@pytest.mark.parametrize("enabled", [True, False])
def test_setting_reaches_paddle(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    seen: dict[str, Any] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))
    engine = build_ocr_engine(Settings(ocr_engine="paddle", ocr_enable_mkldnn=enabled))
    assert isinstance(engine, PaddleOcrEngine)
    engine._load()
    assert seen["enable_mkldnn"] is enabled
