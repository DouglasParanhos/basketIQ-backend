"""Typed contracts between pipeline stages."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from basketiq.domain.status import Merchant, ProcessingRoute

_SPLIT_AMOUNT = re.compile(r"(\d),\s+(\d{2})(?!\d)")


class TextToken(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0
    page: int = 0


class TextLine(BaseModel):
    """A visual line of the document: tokens sorted left to right."""

    tokens: list[TextToken]
    page: int = 0

    @property
    def text(self) -> str:
        joined = " ".join(t.text for t in self.tokens).strip()
        # OCR often splits amounts at the separator ("18, 17"): rejoin before parsing.
        return _SPLIT_AMOUNT.sub(r"\1,\2", joined)

    @property
    def confidence(self) -> float:
        return min((t.confidence for t in self.tokens), default=0.0)

    @property
    def y(self) -> float:
        return min((t.y0 for t in self.tokens), default=0.0)


class DocumentText(BaseModel):
    lines: list[TextLine]
    route: ProcessingRoute
    engine: str
    page_count: int = 1

    @property
    def mean_confidence(self) -> float:
        tokens = [t for line in self.lines for t in line.tokens]
        if not tokens:
            return 0.0
        return sum(t.confidence for t in tokens) / len(tokens)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class ParsedLine(BaseModel):
    raw_description: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price_cents: int | None = None
    line_total_cents: int | None = None
    discount_cents: int = 0
    tax_code: str | None = None
    # Per-field confidence in [0, 1]; keys: description, quantity, unit_price, line_total, discount.
    field_confidence: dict[str, float] = Field(default_factory=dict)
    # Source text that supports the values (shown in the review UI, never logged).
    evidence: list[str] = Field(default_factory=list)
    # True when the parser could not interpret the line: it must go to review, never disappear.
    unresolved: bool = False
    resolved_by: str = "parser"

    @property
    def confidence(self) -> float:
        return min(self.field_confidence.values(), default=0.0)


class ParsedReceipt(BaseModel):
    merchant: Merchant
    purchase_at: datetime | None = None
    receipt_ref: str | None = None
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    total_cents: int | None = None
    # Sum of savings printed on the receipt (positive number), if the receipt states it.
    total_discount_cents: int | None = None
    lines: list[ParsedLine] = Field(default_factory=list)
    # Per-field confidence for header fields: merchant, purchase_at, total, subtotal, tax.
    field_confidence: dict[str, float] = Field(default_factory=dict)
    parser_version: str
    notes: list[str] = Field(default_factory=list)


class ReviewReason(BaseModel):
    code: str
    message: str
    field: str | None = None
    line_index: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
