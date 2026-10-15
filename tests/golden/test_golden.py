"""Golden-set regression (plano de testes §6, §13). OCR cases run only with -m ocr."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from basketiq.eval.golden import regressions, run

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
def test_synthetic_golden_set_with_local_ocr() -> None:
    os.environ["BASKETIQ_OCR_ENGINE"] = "paddle"
    from basketiq.config import get_settings

    get_settings.cache_clear()
    try:
        report = run([HERE / "synthetic"], use_ocr=True)
    finally:
        os.environ["BASKETIQ_OCR_ENGINE"] = "none"
        get_settings.cache_clear()
    problems = regressions(report, json.loads((HERE / "baseline_ocr.json").read_text()))
    assert problems == []


@pytest.mark.golden_real
def test_real_receipts(request: pytest.FixtureRequest) -> None:
    real = os.environ.get("GOLDEN_REAL_DIR")
    if not real:
        pytest.skip("GOLDEN_REAL_DIR not set (real receipts are never committed)")
    os.environ["BASKETIQ_OCR_ENGINE"] = "paddle"
    from basketiq.config import get_settings

    get_settings.cache_clear()
    try:
        report = run([Path(real)], use_ocr=True)
    finally:
        os.environ["BASKETIQ_OCR_ENGINE"] = "none"
        get_settings.cache_clear()
    failures = {m: s["failures"] for m, s in report["per_merchant"].items() if s["failures"]}
    assert failures == {}
