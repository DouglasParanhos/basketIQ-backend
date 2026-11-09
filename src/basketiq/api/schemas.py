from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, field_validator
from pydantic_core import PydanticCustomError

from basketiq.domain.categories import CATEGORY_IDS
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


# ---------------------------------------------------------------- passkeys (RF-01, spec 016)


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None


# ---------------------------------------------------------------- profile (RF-13, RF-14)


class ProfileOut(BaseModel):
    household_size: int | None
    merchants: list[str]
    goals: list[Goal]
    monthly_goal_cents: int | None
    onboarding_completed_at: datetime | None
    passkey_offer_dismissed_at: datetime | None


class ProfilePatch(BaseModel):
    """Only the fields present in the request are applied (`model_fields_set`): omitted keeps the value,
    `null` clears the scalar fields. Lists are never `null`: `[]` clears them."""

    model_config = ConfigDict(extra="forbid")

    household_size: StrictInt | None = None
    merchants: list[str] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    monthly_goal_cents: StrictInt | None = None  # whole cents, never a decimal (constitution II)
    onboarding_completed: Literal[True] = True
    passkey_offer_dismissed: Literal[True] = True  # the one-time passkey offer on the Home (spec 016)

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


class ArticlePrice(BaseModel):
    price_cents: int | None
    date: str
    discounted: bool


class ListArticle(BaseModel):
    """One product at one store: its purchases and the prices paid there, in the item's base unit."""

    merchant: str | None  # supported merchant id; None for any other store
    merchant_name: str
    purchases: int
    avg_price_cents: int | None
    lowest: ArticlePrice | None
    highest: ArticlePrice | None
    last: ArticlePrice | None


class ListItem(BaseModel):
    product_id: str
    name: str
    purchases: int
    packages: str
    packages_per_month: float
    avg_interval_days: float | None
    spent_cents: int
    price_basis: Literal["un", "kg", "l"]
    mixed_units: bool
    avg_price_cents: int | None
    last_purchase: str
    articles: list[ListArticle]


class ListArticlesOut(BaseModel):
    window_days: int
    date_from: str
    date_to: str
    items: list[ListItem]
    excluded_lines: int


# ---------------------------------------------------------------- shopping list (RF-16)


class CategoryOut(BaseModel):
    id: str
    name: str


class ExpectedPriceOut(BaseModel):
    """What the person last paid for the product: at the chosen supermarket, or else at another one."""

    price_cents: int
    price_basis: Literal["un", "kg", "l"]
    date: str
    merchant: str | None
    merchant_name: str
    other_merchant: bool
    source: Literal["own_receipts"]


class ShoppingItemOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    category_name: str
    quantity: str
    unit: str | None
    checked: bool
    already_have: bool
    product_id: uuid.UUID | None
    expected: ExpectedPriceOut | None


class EstimateOut(BaseModel):
    """The list's estimate at one supermarket. Values from other supermarkets are counted, never hidden."""

    merchant: str
    merchant_name: str
    total_cents: int
    items_with_price: int
    other_merchant_values: int


class ShoppingListOut(BaseModel):
    merchant: str
    categories: list[CategoryOut]
    items: list[ShoppingItemOut]
    estimates: list[EstimateOut]
    items_total: int
    checked_count: int
    already_have_excluded: int


def _known_category(value: str) -> str:
    if value not in CATEGORY_IDS:
        raise PydanticCustomError("category", "Categoria inválida.")
    return value


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise PydanticCustomError("name", "Indique o produto.")
    return value


Category = Annotated[str, AfterValidator(_known_category)]
ItemName = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(_clean_name)]
ItemQuantity = Annotated[Decimal, Field(gt=0, lt=10000, max_digits=7, decimal_places=3)]
ItemUnit = Literal["un", "kg", "g", "l", "ml"]


class ShoppingItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ItemName
    quantity: ItemQuantity = Decimal(1)
    unit: ItemUnit | None = None
    category: Category | None = None
    already_have: bool = False
    product_id: uuid.UUID | None = None


class ShoppingItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ItemName | None = None
    quantity: ItemQuantity | None = None
    unit: ItemUnit | None = None
    category: Category | None = None
    checked: bool | None = None
    already_have: bool | None = None


class ShoppingMerchantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: Merchant

    @field_validator("merchant")
    @classmethod
    def _supported(cls, value: Merchant) -> Merchant:
        if value not in SUPPORTED_MERCHANTS:
            raise PydanticCustomError("merchant", "Supermercado não suportado.")
        return value


class EvolutionPointOut(BaseModel):
    date: str
    price_cents: int
    quantity: str | None
    merchant: str | None
    merchant_name: str
    origin: Literal["own", "base"]


class EvolutionOut(BaseModel):
    """The price history of a list item. `has_history` is false when there is nothing to show."""

    has_history: bool
    price_basis: Literal["un", "kg", "l"] | None
    expected: ExpectedPriceOut | None
    last_cents: int | None
    min_cents: int | None
    max_cents: int | None
    points: list[EvolutionPointOut]


class SuggestionOut(BaseModel):
    product_id: uuid.UUID
    name: str
    category: str
    category_name: str


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
