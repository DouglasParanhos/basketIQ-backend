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

NIF_WEIGHT = 0.95

# The tax number of each chain's company (public company data), as printed on its receipts. The same number is
# evidence when it is read from the text and when it is read from the fiscal QR code (field A).
CHAIN_NIFS: dict[Merchant, str] = {
    Merchant.PINGO_DOCE: "500829993",
    Merchant.LIDL: "503340855",
    Merchant.CONTINENTE: "502011475",
    Merchant.AUCHAN: "503003808",
    Merchant.ALDI: "516245708",
    Merchant.MERCADONA: "514038942",
    Merchant.EL_CORTE_INGLES: "501810285",
}


def _nif_signal(merchant: Merchant) -> tuple[re.Pattern[str], float, bool]:
    """The number as the OCR may print it: in groups of three apart by a space or a dot, a zero read as O."""
    nif = CHAIN_NIFS[merchant]
    groups = [nif[i : i + 3].replace("0", "[0O]") for i in range(0, len(nif), 3)]
    return re.compile(r"[\s.]?".join(groups)), NIF_WEIGHT, False


# (pattern, weight, header_only). Weights are independent evidence strengths.
_SIGNALS: dict[Merchant, list[tuple[re.Pattern[str], float, bool]]] = {
    Merchant.PINGO_DOCE: [
        (re.compile(r"PINGO\s*DOCE"), 0.9, False),
        _nif_signal(Merchant.PINGO_DOCE),
        (re.compile(r"PINGODOCE\.PT"), 0.6, False),
        (re.compile(r"POUPA\s*MAIS"), 0.5, False),
    ],
    Merchant.LIDL: [
        (re.compile(r"\bLIDL\b"), 0.9, True),
        _nif_signal(Merchant.LIDL),
        (re.compile(r"LIDL\.PT"), 0.6, False),
        (re.compile(r"LIDL\s*PLUS"), 0.5, False),
    ],
    Merchant.CONTINENTE: [
        (re.compile(r"\bCONTINENTE\b"), 0.9, True),
        (re.compile(r"MODELO\s+CONTINENTE"), 0.95, False),
        (re.compile(r"CARTAO\s+CONTINENTE"), 0.6, False),
        _nif_signal(Merchant.CONTINENTE),
    ],
    Merchant.AUCHAN: [
        (re.compile(r"\bAUCHAN\b"), 0.9, True),
        (re.compile(r"AUCHAN\.PT"), 0.6, False),
        _nif_signal(Merchant.AUCHAN),
    ],
    Merchant.ALDI: [
        (re.compile(r"\bALDI\b"), 0.9, True),
        (re.compile(r"ALDI\.PT"), 0.6, False),
        _nif_signal(Merchant.ALDI),
    ],
    Merchant.MERCADONA: [
        (re.compile(r"\bMERCADONA\b"), 0.9, True),
        (re.compile(r"IRMADONA\s+SUPERMERCADOS"), 0.9, False),  # company name
        _nif_signal(Merchant.MERCADONA),
    ],
    Merchant.EL_CORTE_INGLES: [
        (re.compile(r"EL\s+CORTE\s+INGLES"), 0.9, True),
        (re.compile(r"ELCORTEINGLES\.PT"), 0.6, False),
        _nif_signal(Merchant.EL_CORTE_INGLES),
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


def classify(
    doc: DocumentText, hint: Merchant | None = None, issuer_nif: str | None = None
) -> Classification:
    """`issuer_nif` is the tax number read from the fiscal QR code: evidence independent of the text."""
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

    chain = next((m for m, nif in CHAIN_NIFS.items() if nif == issuer_nif), None)
    if chain is not None:
        score, found = scores[chain]
        scores[chain] = (1 - (1 - score) * (1 - NIF_WEIGHT), [*found, "qr_nif"])

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
