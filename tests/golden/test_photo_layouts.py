"""Photos of invented receipts in three layouts, each damaged four ways, read by the generic parser
alone (spec 009, A2.2). They prove the layouts that were thought of, not unforeseen ones: the blind set
is for those."""

from __future__ import annotations

from pathlib import Path

import pytest

from basketiq.eval.golden import run
from basketiq.pipeline.ocr import OcrEngine
from tests.golden import photo_layouts


@pytest.mark.ocr
@pytest.mark.req("F-50")
def test_invented_layouts_photographed_four_ways_are_read(tmp_path: Path, paddle_engine: OcrEngine) -> None:
    names = photo_layouts.write_all(tmp_path)
    assert len(names) == 12
    report = run([tmp_path], use_ocr=True, engine=paddle_engine, unseen=True, no_qr=True)
    overall = report["overall"]
    assert overall["total_accuracy"] == 1.0
    assert overall["line_recall"] >= 0.9
    assert overall["line_precision"] >= 0.9
