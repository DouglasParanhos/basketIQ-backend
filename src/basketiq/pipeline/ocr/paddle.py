"""Local OCR with PaddleOCR (PP-OCRv5, latin recognition model covers Portuguese)."""

from __future__ import annotations

import argparse
import os
import threading
from typing import Any

import numpy as np
from PIL import Image

from basketiq.pipeline.schema import TextToken

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class PaddleOcrEngine:
    def __init__(self, det_model: str, rec_model: str) -> None:
        self.name = f"paddleocr:{det_model}+{rec_model}"
        self._det_model = det_model
        self._rec_model = rec_model
        self._ocr: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._ocr is None:
                from paddleocr import PaddleOCR

                self._ocr = PaddleOCR(
                    text_detection_model_name=self._det_model,
                    text_recognition_model_name=self._rec_model,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    # oneDNN kernels in paddle 3.3 fail on PP-OCRv5 ("ConvertPirAttribute2RuntimeAttribute").
                    enable_mkldnn=False,
                )
        return self._ocr

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        ocr = self._load()
        array = np.asarray(image.convert("RGB"))[:, :, ::-1]  # PaddleOCR expects BGR
        with self._lock:  # the predictor is not thread-safe
            results = ocr.predict(array)
        tokens: list[TextToken] = []
        for result in results:
            data = result.json["res"]
            for text, score, box, poly in zip(
                data["rec_texts"], data["rec_scores"], data["rec_boxes"], data["rec_polys"], strict=False
            ):
                if not text.strip():
                    continue
                x0, y0, x1, y1 = (float(v) for v in box)
                tokens.append(
                    TextToken(
                        text=text,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        confidence=float(score),
                        page=page,
                        slope=_baseline_slope(poly),
                    )
                )
        return tokens


def _baseline_slope(poly: Any) -> float | None:
    """dy/dx of the top edge of a detected text polygon (points ordered top-left, top-right, ...)."""
    (x0, y0), (x1, y1) = poly[0], poly[1]
    return float(y1 - y0) / float(x1 - x0) if x1 > x0 else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", action="store_true", help="download models and run once")
    args = parser.parse_args()
    if args.warmup:
        from basketiq.config import get_settings

        s = get_settings()
        engine = PaddleOcrEngine(s.ocr_det_model, s.ocr_rec_model)
        engine.recognize(Image.new("RGB", (200, 60), "white"))
        print("PaddleOCR models ready")


if __name__ == "__main__":
    main()
