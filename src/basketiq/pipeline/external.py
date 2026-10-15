"""Controlled external OCR/AI fallback (spec técnica §5 passo 7 and §11).

No concrete provider is chosen yet; this module defines the adapter and the rules that decide
whether a call may happen. The default is disabled, so documents never leave the platform.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from basketiq.domain.status import Merchant
from basketiq.pipeline.schema import ParsedReceipt


class FallbackReason(StrEnum):
    LOW_OCR_QUALITY = "LOW_OCR_QUALITY"
    MERCHANT_UNKNOWN = "MERCHANT_UNKNOWN"
    PARSER_FAILED = "PARSER_FAILED"
    USER_REQUESTED = "USER_REQUESTED"


class ExternalUnavailableError(RuntimeError):
    pass


class ExternalExtractor(Protocol):
    name: str
    cost_per_call_cents: int

    def extract(self, data: bytes, mime: str, timeout: float) -> ParsedReceipt: ...


class MockExternalExtractor:
    """Stand-in provider for local runs and tests. Returns nothing it cannot know."""

    name = "mock-external"
    cost_per_call_cents = 5

    def extract(self, data: bytes, mime: str, timeout: float) -> ParsedReceipt:
        return ParsedReceipt(
            merchant=Merchant.UNKNOWN, parser_version="mock-external-1", notes=["mock_provider"]
        )


def fallback_reason(
    *,
    ocr_mean_confidence: float | None,
    ocr_threshold: float,
    merchant: Merchant,
    parsed_lines: int,
    user_requested: bool,
) -> FallbackReason | None:
    if user_requested:
        return FallbackReason.USER_REQUESTED
    if ocr_mean_confidence is not None and ocr_mean_confidence < ocr_threshold:
        return FallbackReason.LOW_OCR_QUALITY
    if merchant == Merchant.UNKNOWN:
        return FallbackReason.MERCHANT_UNKNOWN
    if parsed_lines == 0:
        return FallbackReason.PARSER_FAILED
    return None


def build_external(mode: str) -> ExternalExtractor | None:
    if mode == "mock":
        return MockExternalExtractor()
    return None
