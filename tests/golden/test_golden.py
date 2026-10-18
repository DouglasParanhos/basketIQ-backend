"""Golden-set regression (plano de testes §6, §13). OCR cases need the PaddleOCR models (marker `ocr`)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from basketiq.eval.golden import regressions, run
from basketiq.pipeline.ocr import OcrEngine

HERE = Path(__file__).resolve().parent


@pytest.mark.req("PT-6")
def test_synthetic_golden_set_without_ocr_has_no_regressions() -> None:
    report = run([HERE / "synthetic"], use_ocr=False)
    problems = regressions(report, json.loads((HERE / "baseline.json").read_text()))
    assert problems == []
    for merchant, metrics in report["per_merchant"].items():
        assert metrics["failures"] == [], merchant


@pytest.mark.ocr
@pytest.mark.req("PT-6")
def test_synthetic_golden_set_with_local_ocr(paddle_engine: OcrEngine) -> None:
    report = run([HERE / "synthetic"], use_ocr=True, engine=paddle_engine)
    problems = regressions(report, json.loads((HERE / "baseline_ocr.json").read_text()))
    assert problems == []


@pytest.mark.golden_real
def test_real_receipts(paddle_engine: OcrEngine) -> None:
    real = os.environ.get("GOLDEN_REAL_DIR")
    if not real:
        pytest.skip("GOLDEN_REAL_DIR not set (real receipts are never committed)")
    report = run([Path(real)], use_ocr=True, engine=paddle_engine)
    failures = {m: s["failures"] for m, s in report["per_merchant"].items() if s["failures"]}
    assert failures == {}
