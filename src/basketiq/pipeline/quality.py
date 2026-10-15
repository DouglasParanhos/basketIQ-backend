"""Non-blocking image quality checks (RF-05): warn and suggest a new photo, never reject."""

from __future__ import annotations

import numpy as np
from PIL import Image

BLUR_VARIANCE_MIN = 60.0
DARK_MEAN_MAX = 70.0
MIN_TOKENS = 15
LOW_OCR_CONFIDENCE = 0.75

WARNING_MESSAGES = {
    "blurry": "A imagem parece desfocada.",
    "too_dark": "A imagem está demasiado escura.",
    "low_text": "Foi encontrado pouco texto; a nota pode estar cortada.",
    "low_ocr_confidence": "Parte do texto foi lida com pouca confiança.",
}


def image_warnings(image: Image.Image) -> list[str]:
    gray = image.convert("L")
    gray.thumbnail((1200, 1200))
    arr = np.asarray(gray, dtype=np.float32)
    warnings: list[str] = []
    if _laplacian_variance(arr) < BLUR_VARIANCE_MIN:
        warnings.append("blurry")
    if float(arr.mean()) < DARK_MEAN_MAX:
        warnings.append("too_dark")
    return warnings


def ocr_warnings(token_count: int, mean_confidence: float) -> list[str]:
    warnings: list[str] = []
    if token_count < MIN_TOKENS:
        warnings.append("low_text")
    if token_count and mean_confidence < LOW_OCR_CONFIDENCE:
        warnings.append("low_ocr_confidence")
    return warnings


def _laplacian_variance(arr: np.ndarray) -> float:
    lap = -4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2] + arr[1:-1, 2:]
    return float(lap.var())
