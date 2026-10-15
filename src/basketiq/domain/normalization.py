"""Deterministic product-name normalisation. The original description is always kept on the line."""

from __future__ import annotations

import re
import unicodedata


def normalize_key(raw: str) -> str:
    """Matching key: accents removed, upper case, punctuation collapsed ("Pão  croc." == "PAO CROC")."""
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = re.sub(r"[^A-Z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:255]


def canonical_name(raw: str) -> str:
    """Display name for an automatically created product: the printed name, whitespace cleaned."""
    return re.sub(r"\s+", " ", raw).strip()[:255]
