"""Local OCR with PaddleOCR (PP-OCRv5, latin recognition model covers Portuguese)."""

from __future__ import annotations

import argparse
import math
import os
import threading
from collections import defaultdict
from typing import Any

import numpy as np
from PIL import Image

from basketiq.pipeline.schema import TextToken

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

ORIENTATION_MODEL = "PP-LCNet_x1_0_doc_ori"
ORIENTATION_WINDOWS = 5  # square windows read along a long receipt; their votes decide
ORIENTATION_MIN_SHARE = 0.5  # of the summed scores: below this the photo is left as it is


class PaddleOcrEngine:
    def __init__(self, det_model: str, rec_model: str, enable_mkldnn: bool) -> None:
        self.name = f"paddleocr:{det_model}+{rec_model}"
        self._det_model = det_model
        self._rec_model = rec_model
        self._enable_mkldnn = enable_mkldnn
        self._ocr: Any = None
        self._classifier: Any = None
        self._line_reader: Any = None
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
                    # oneDNN is ~3x faster on CPU; paddlepaddle 3.3.1 breaks on it (pyproject pins 3.2.2).
                    enable_mkldnn=self._enable_mkldnn,
                )
        return self._ocr

    def _load_classifier(self) -> Any:
        with self._lock:
            if self._classifier is None:
                from paddleocr import DocImgOrientationClassification

                self._classifier = DocImgOrientationClassification(
                    model_name=ORIENTATION_MODEL, enable_mkldnn=self._enable_mkldnn
                )
        return self._classifier

    def orientation(self, image: Image.Image) -> int:
        """The page-orientation model looks at a 224 px thumbnail, where a whole long receipt is a blur: it
        reads square windows along the receipt instead, and each window votes with its score."""
        classifier = self._load_classifier()
        votes: dict[int, float] = defaultdict(float)
        for window in _square_windows(image):
            with self._lock:
                result = next(iter(classifier.predict(_bgr(window)))).json["res"]
            votes[int(result["label_names"][0])] += float(result["scores"][0])
        angle, weight = max(votes.items(), key=lambda vote: vote[1])
        return angle if weight >= ORIENTATION_MIN_SHARE * sum(votes.values()) else 0

    def _load_line_reader(self) -> Any:
        with self._lock:
            if self._line_reader is None:
                from paddleocr import TextRecognition

                self._line_reader = TextRecognition(
                    model_name=self._rec_model, enable_mkldnn=self._enable_mkldnn
                )
        return self._line_reader

    def read_line(self, image: Image.Image) -> tuple[str, float]:
        reader = self._load_line_reader()
        with self._lock:
            result = next(iter(reader.predict(_bgr(image)))).json["res"]
        return str(result["rec_text"]), float(result["rec_score"])

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        ocr = self._load()
        with self._lock:  # the predictor is not thread-safe
            results = ocr.predict(_bgr(image))
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


def _bgr(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"))[:, :, ::-1]  # PaddleOCR expects BGR


def _square_windows(image: Image.Image) -> list[Image.Image]:
    """Up to ORIENTATION_WINDOWS squares as wide as the receipt, spread evenly along it."""
    side = min(image.size)
    long = max(image.size)
    count = min(ORIENTATION_WINDOWS, math.ceil(long / side))
    starts = [round(i * (long - side) / max(count - 1, 1)) for i in range(count)]
    if image.width >= image.height:
        return [image.crop((s, 0, s + side, side)) for s in starts]
    return [image.crop((0, s, side, s + side)) for s in starts]


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
        engine = PaddleOcrEngine(s.ocr_det_model, s.ocr_rec_model, s.ocr_enable_mkldnn)
        engine.recognize(Image.new("RGB", (200, 60), "white"))
        engine.orientation(Image.new("RGB", (200, 60), "white"))
        engine.read_line(Image.new("RGB", (200, 60), "white"))
        print("PaddleOCR models ready")


if __name__ == "__main__":
    main()
