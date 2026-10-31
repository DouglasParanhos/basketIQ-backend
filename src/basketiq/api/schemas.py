from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pydantic_core import PydanticCustomError

from basketiq.domain.status import (
    HOUSEHOLD_SIZE_RANGE,
    MONTHLY_GOAL_CENTS_RANGE,
    SUPPORTED_MERCHANTS,
    Goal,
    Merchant,
    ReceiptStatus,
)

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


# ---------------------------------------------------------------- profile (RF-13, RF-14)


class ProfileOut(BaseModel):
    household_size: int | None
    merchants: list[str]
    goals: list[Goal]
    monthly_goal_cents: int | None
    onboarding_completed_at: datetime | None


class ProfilePatch(BaseModel):
    """Only the fields present in the request are applied (`model_fields_set`): omitted keeps the value,
    `null` clears the scalar fields. Lists are never `null`: `[]` clears them."""

    model_config = ConfigDict(extra="forbid")

    household_size: StrictInt | None = None
    merchants: list[str] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    monthly_goal_cents: StrictInt | None = None  # whole cents, never a decimal (constitution II)
    onboarding_completed: Literal[True] = True

    @field_validator("household_size")
    @classmethod
    def _household_size(cls, value: int | None) -> int | None:
        low, high = HOUSEHOLD_SIZE_RANGE
        if value is not None and not low <= value <= high:
            raise PydanticCustomError(
                "out_of_range", "Indique entre {low} e {high} pessoas.", {"low": low, "high": high}
            )
        return value

    @field_validator("monthly_goal_cents")
    @classmethod
    def _monthly_goal(cls, value: int | None) -> int | None:
        low, high = MONTHLY_GOAL_CENTS_RANGE
        if value is not None and not low <= value <= high:
            raise PydanticCustomError(
                "out_of_range",
                "A meta pessoal vai de {low} € a {high} €.",
                {"low": low // 100, "high": high // 100},
            )
        return value

    @field_validator("merchants")
    @classmethod
    def _merchants(cls, value: list[str]) -> list[str]:
        for merchant in value:
            if merchant not in SUPPORTED_MERCHANTS:
                raise PydanticCustomError(
                    "unsupported_merchant", "Mercado não suportado: {merchant}.", {"merchant": merchant}
                )
        return list(dict.fromkeys(value))

    @field_validator("goals")
    @classmethod
    def _goals(cls, value: list[Goal]) -> list[Goal]:
        return list(dict.fromkeys(value))


class MerchantOut(BaseModel):
    id: str
    name: str


# ---------------------------------------------------------------- stored original files (RF-12)


class StoredFilesOut(BaseModel):
    files: int


class StoredFilesDeleted(BaseModel):
    files_deleted: int
    receipts: int
    remaining: int


# ---------------------------------------------------------------- uploads


class FileRequirements(BaseModel):
    max_bytes: int
    accepted_types: list[str]
    max_pdf_pages: int
    max_photos: int


class UploadInitIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=64)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    merchant_hint: Merchant | None = None
    allow_duplicate: bool = False


class PhotoAddIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=64)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


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
    merchant_name: str | None
    purchase_at: datetime | None
    receipt_ref: str | None
    store_label: str | None
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
    photo_count: int
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
    merchant_name: str | None
    store_label: str | None
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
    merchant_name: str | None = Field(default=None, max_length=80)
    purchase_at: datetime | None = None
    total_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    lines: list[LinePatchIn] | None = Field(default=None, max_length=500)

    @field_validator("merchant_name")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if isinstance(v, str) else v


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
    avg_price_cents: int | None
    price_basis: Literal["un", "kg", "l"]
    purchases: int
    merchants: int


class WeekOut(BaseModel):
    index: int
    from_: Date = Field(alias="from")
    to: Date
    spent_cents: int

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class MerchantShareOut(BaseModel):
    merchant: str | None
    name: str
    spent_cents: int
    purchases: int
    share_pct: int


class RecentReceiptOut(BaseModel):
    id: uuid.UUID
    merchant: str | None
    name: str
    date: Date
    total_cents: int | None
    status: ReceiptStatus


class OverviewOut(BaseModel):
    month: str
    spent_cents: int
    goal_cents: int | None
    goal_pct: int | None
    band: Literal["ok", "attention", "high", "none"]
    monthly_average_cents: int | None
    recent_vs_average_pct: int | None
    average_trend: Literal["below", "on_average", "above"] | None
    average_months: int
    average_min_months: int
    purchases_this_month: int
    purchases_last_month: int
    weeks: list[WeekOut]
    by_merchant: list[MerchantShareOut]
    recent_receipts: list[RecentReceiptOut]
    empty: bool


class MonthTotalOut(BaseModel):
    month: str
    confirmed_count: int
    total_cents: int


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
