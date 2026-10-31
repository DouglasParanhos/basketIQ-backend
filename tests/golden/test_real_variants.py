"""The real receipts captured in the ways nobody sent yet: at an angle, sideways, from further away, as a
photo of a PDF-only shop, as a PDF of a photo-only shop (see `real_variants`). Each variant must read like
the original: merchant, date, total and every line. Local only, like `test_real_receipts`:

    make golden-real-variants GOLDEN_REAL_DIR=../Receipts            # all variants (~100 OCR reads, ~20 min)
    make golden-real-variants GOLDEN_REAL_DIR=../Receipts K=sideways # one of them
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from basketiq.eval.golden import discover, run
from basketiq.pipeline.ocr import OcrEngine
from tests.golden import real_variants


@pytest.mark.golden_real
@pytest.mark.parametrize("variant", real_variants.VARIANTS)
def test_real_receipts_read_alike_in_every_capture(
    variant: str, tmp_path: Path, paddle_engine: OcrEngine
) -> None:
    real = os.environ.get("GOLDEN_REAL_DIR")
    if not real:
        pytest.skip("GOLDEN_REAL_DIR not set (real receipts are never committed)")
    written = real_variants.write_variant(discover(Path(real)), variant, tmp_path, paddle_engine)
    assert written
    report = run([tmp_path], use_ocr=True, engine=paddle_engine)
    failures = {m: s["failures"] for m, s in report["per_merchant"].items() if s["failures"]}
    assert failures == {}
