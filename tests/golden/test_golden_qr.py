"""The fiscal QR code on the real receipts (kept outside git): spec 009, A1. Needs GOLDEN_REAL_DIR."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from basketiq.eval.golden import run
from basketiq.pipeline.ocr import OcrEngine
from basketiq.pipeline.qr import QR_BUDGET_S


@pytest.mark.golden_real
@pytest.mark.req("F-39")
@pytest.mark.req("F-42")
def test_real_receipts_qr(paddle_engine: OcrEngine) -> None:
    real = os.environ.get("GOLDEN_REAL_DIR")
    if not real:
        pytest.skip("GOLDEN_REAL_DIR not set (real receipts are never committed)")
    report = run([Path(real)], use_ocr=True, engine=paddle_engine)
    overall = report["overall"]
    documents = overall["documents"]
    read = round(overall["qr_read_rate"] * documents)
    assert read >= 11, f"only {read} of {documents} QR codes were read"
    # Every QR that was read agrees with the receipt: same day, and a total that the relation explains.
    assert overall["qr_date_accuracy"] == 1.0
    assert overall["qr_total_accuracy"] == 1.0
    # Reading is cheap: well inside the budget for every document, the slowest photo included.
    slowest = max(case["qr_ms"] for case in report["cases"] if case["qr_read"])
    assert slowest < QR_BUDGET_S * 1000
