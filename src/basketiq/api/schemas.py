from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from basketiq.domain.status import Merchant, ReceiptStatus

# ---------------------------------------------------------------- account


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    privacy_accepted_at: datetime | None
    privacy_version: str | None
    current_privacy_version: str
    created_at: datetime


class DeletionResult(BaseModel):
    receipts_deleted: int
    objects_deleted: int


# ---------------------------------------------------------------- uploads


class FileRequirements(BaseModel):
    max_bytes: int
    accepted_types: list[str]
    max_pdf_pages: int


class UploadInitIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=64)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    merchant_hint: Merchant | None = None
    allow_duplicate: bool = False


class PresignedUploadOut(BaseModel):
    url: str
    fields: dict[str, str]
    expires_at: datetime


class UploadInitOut(BaseModel):
    receipt_id: uuid.UUID
    upload: PresignedUploadOut
    requirements: FileRequirements


class ProcessIn(BaseModel):
    external: bool = Field(default=False, description="User explicitly asks for external reprocessing")


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    attempt_count: int
    processing_route: str | None
    created_at: datetime


# ---------------------------------------------------------------- receipts


class ProductRef(BaseModel):
    id: uuid.UUID
    name: str


class LineOut(BaseModel):
    id: uuid.UUID
    line_number: int
    raw_description: str
    product: ProductRef | None
    quantity: Decimal | None
    unit: str | None
    unit_price_cents: int | None
    line_total_cents: int | None
    discount_cents: int
    net_cents: int | None
    tax_code: str | None
    confidence: float | None
    field_confidence: dict[str, float]
    review_status: str
    flags: list[str]
    unresolved: bool
    manually_edited: bool
    edited_fields: list[str]
    evidence: list[str]


class ReviewReasonOut(BaseModel):
    code: str | None
    message: str | None
    field: str | None = None
    line_index: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class WarningOut(BaseModel):
    code: str
    message: str


class ErrorInfo(BaseModel):
    code: str | None
    message: str | None
    action: str | None


class Totals(BaseModel):
    subtotal_cents: int | None
    tax_cents: int | None
    printed_total_cents: int | None
    lines_total_cents: int
    discounts_cents: int
    recalculated_total_cents: int
    difference_cents: int | None
    reconciled: bool


class ProcessingInfo(BaseModel):
    job_id: uuid.UUID
    status: str
    route: str | None
    attempts: int


class ReceiptOut(BaseModel):
    id: uuid.UUID
    status: ReceiptStatus
    error: ErrorInfo | None
    merchant: str | None
    merchant_hint: str | None
    purchase_at: datetime | None
    receipt_ref: str | None
    currency: str
    source_type: str
    totals: Totals
    overall_confidence: float | None
    field_confidence: dict[str, float]
    review_reasons: list[ReviewReasonOut]
    quality_warnings: list[WarningOut]
    manually_edited_fields: list[str]
    lines: list[LineOut]
    duplicate_of_receipt_id: uuid.UUID | None
    duplicate_acknowledged: bool
    can_confirm: bool
    confirmed_at: datetime | None
    confirmed_by: str | None
    original_available: bool
    original_expires_at: datetime | None
    original_deleted_at: datetime | None
    processing: ProcessingInfo | None
    version: int
    created_at: datetime
    updated_at: datetime


class ReceiptSummaryOut(BaseModel):
    id: uuid.UUID
    status: ReceiptStatus
    merchant: str | None
    purchase_at: datetime | None
    total_cents: int | None
    line_count: int
    overall_confidence: float | None
    needs_action: bool
    created_at: datetime


class ReceiptPage(BaseModel):
    items: list[ReceiptSummaryOut]
    next_cursor: str | None


class LinePatchIn(BaseModel):
    id: uuid.UUID | None = None
    raw_description: str | None = Field(default=None, max_length=500)
    product_id: uuid.UUID | None = None
    product_name: str | None = Field(default=None, max_length=255)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    unit: Literal["un", "kg", "g", "l", "ml"] | None = None
    unit_price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    line_total_cents: int | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    discount_cents: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("raw_description", "product_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v


class ReceiptPatchIn(BaseModel):
    version: int
    merchant: Merchant | None = None
    purchase_at: datetime | None = None
    total_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    lines: list[LinePatchIn] | None = Field(default=None, max_length=500)


class VersionIn(BaseModel):
    version: int


class DuplicateDecisionIn(BaseModel):
    action: Literal["keep", "discard"]


class MerchantChoiceIn(BaseModel):
    merchant: Merchant | Literal["UNSUPPORTED"]


class OriginalUrlOut(BaseModel):
    url: str
    expires_in_seconds: int
    content_type: str


# ---------------------------------------------------------------- analytics


class VariationOut(BaseModel):
    total_spent: float | None
    purchases: float | None
    units: float | None


class PreviousPeriodOut(BaseModel):
    from_: str = Field(alias="from")
    to: str
    total_spent_cents: int
    purchases: int
    units: str

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class CardsOut(BaseModel):
    total_spent_cents: int
    purchases: int
    units: str
    articles: int
    variation_pct: VariationOut
    previous_period: PreviousPeriodOut


class SeriesPoint(BaseModel):
    period: str
    spent_cents: int


class SeriesOut(BaseModel):
    granularity: Literal["day", "month"]
    points: list[SeriesPoint]
    insufficient_data: bool


class MerchantShare(BaseModel):
    merchant: str | None
    spent_cents: int
    purchases: int
    share_pct: float


class ProductRanking(BaseModel):
    product_id: str | None
    name: str
    spent_cents: int
    units: str
    lines: int


class SummaryFilters(BaseModel):
    from_: str = Field(alias="from")
    to: str
    merchant: str | None
    product_id: str | None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class SummaryOut(BaseModel):
    filters: SummaryFilters
    cards: CardsOut
    series: SeriesOut
    by_merchant: list[MerchantShare]
    top_products_by_value: list[ProductRanking]
    top_products_by_quantity: list[ProductRanking]
    empty: bool


class ConsumptionItem(BaseModel):
    product_id: str
    name: str | None
    purchases: int
    units: str
    units_per_month: float
    spent_cents: int
    avg_interval_days: float | None
    first_purchase: str
    last_purchase: str


class ConsumptionOut(BaseModel):
    filters: SummaryFilters
    items: list[ConsumptionItem]
    empty: bool


class ProductSearchItem(BaseModel):
    id: str
    name: str
    unit_type: str | None
    lines: int
    spent_cents: int
    last_purchase_at: str | None


class ProductInfo(BaseModel):
    id: str
    name: str
    unit_type: str | None


class PricePoint(BaseModel):
    receipt_id: str
    date: str | None
    merchant: str | None
    quantity: str | None
    unit: str | None
    unit_price_cents: int | None
    price_basis: str
    net_cents: int
    raw_description: str


class MerchantPrice(BaseModel):
    merchant: str
    purchases: int
    avg_price_cents: int | None
    min_price_cents: int | None
    max_price_cents: int | None
    last_price_cents: int | None
    last_date: str | None


class ProductAnalyticsOut(BaseModel):
    product: ProductInfo
    price_basis: str
    mixed_units: bool
    total_quantity: str
    total_spent_cents: int
    avg_price_cents: int | None
    min_price_cents: int | None
    max_price_cents: int | None
    purchases: int
    history: list[PricePoint]
    by_merchant: list[MerchantPrice]
    insufficient_data: bool
