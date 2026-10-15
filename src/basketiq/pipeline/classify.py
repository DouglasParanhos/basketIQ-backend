"""Merchant classification from deterministic header signals (spec técnica §5, passo 4)."""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel

from basketiq.domain.status import Merchant
from basketiq.pipeline.schema import DocumentText

CLASSIFIER_VERSION = "classify-1"
MIN_CONFIDENCE = 0.6
HEADER_LINES = 20

# (pattern, weight, header_only). Weights are independent evidence strengths.
_SIGNALS: dict[Merchant, list[tuple[re.Pattern[str], float, bool]]] = {
    Merchant.PINGO_DOCE: [
        (re.compile(r"PINGO\s*DOCE"), 0.9, False),
        (re.compile(r"500\s?829\s?993"), 0.95, False),  # NIPC printed on the receipt
        (re.compile(r"PINGODOCE\.PT"), 0.6, False),
        (re.compile(r"POUPA\s*MAIS"), 0.5, False),
    ],
    Merchant.LIDL: [
        (re.compile(r"\bLIDL\b"), 0.9, True),
        (re.compile(r"503\s?340\s?855"), 0.95, False),  # NIF printed on the receipt
        (re.compile(r"LIDL\.PT"), 0.6, False),
        (re.compile(r"LIDL\s*PLUS"), 0.5, False),
    ],
    Merchant.CONTINENTE: [
        (re.compile(r"\bCONTINENTE\b"), 0.9, True),
        (re.compile(r"MODELO\s+CONTINENTE"), 0.95, False),
        (re.compile(r"CARTAO\s+CONTINENTE"), 0.6, False),
    ],
    Merchant.AUCHAN: [
        (re.compile(r"\bAUCHAN\b"), 0.9, True),
        (re.compile(r"AUCHAN\.PT"), 0.6, False),
    ],
}


class Classification(BaseModel):
    merchant: Merchant
    confidence: float
    signals: list[str]
    hint_conflict: bool = False


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).upper()


def classify(doc: DocumentText, hint: Merchant | None = None) -> Classification:
    header = _fold("\n".join(line.text for line in doc.lines[:HEADER_LINES]))
    full = _fold(doc.text)
    scores: dict[Merchant, tuple[float, list[str]]] = {}
    for merchant, signals in _SIGNALS.items():
        miss = 1.0
        found: list[str] = []
        for pattern, weight, header_only in signals:
            haystack = header if header_only else full
            if pattern.search(haystack):
                miss *= 1 - weight
                found.append(pattern.pattern)
        scores[merchant] = (1 - miss, found)

    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    (best, (best_score, best_signals)), (_, (second_score, _)) = ranked[0], ranked[1]
    # Two merchants with strong evidence is suspicious (e.g. a Lidl receipt mentioning Continente).
    confidence = best_score * (1 - 0.5 * second_score)
    detected = best if confidence >= MIN_CONFIDENCE else Merchant.UNKNOWN

    if hint is None or hint == Merchant.UNKNOWN:
        return Classification(merchant=detected, confidence=round(confidence, 3), signals=best_signals)
    if detected == hint:
        return Classification(merchant=hint, confidence=max(round(confidence, 3), 0.95), signals=best_signals)
    if detected == Merchant.UNKNOWN:
        # The user's statement is accepted, but it cannot be corroborated by the document.
        return Classification(merchant=hint, confidence=0.85, signals=["user_hint"])
    return Classification(
        merchant=detected, confidence=round(min(confidence, 0.7), 3), signals=best_signals, hint_conflict=True
    )
