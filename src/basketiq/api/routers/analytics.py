from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from basketiq.api.deps import ConsentedUser, Db, SettingsDep, Today
from basketiq.api.errors import ApiError, FieldError
from basketiq.api.routers.config import AppConfigDep
from basketiq.api.schemas import (
    ConsumptionOut,
    MonthTotalOut,
    OverviewOut,
    ProductAnalyticsOut,
    ProductSearchItem,
    SummaryOut,
)
from basketiq.domain.status import Merchant
from basketiq.observability import metrics
from basketiq.services import analytics
from basketiq.services.products import get_visible

router = APIRouter(prefix="/v1", tags=["analytics"])

MAX_RANGE_DAYS = 3 * 366
MONTH_PATTERN = r"^20\d{2}-(0[1-9]|1[0-2])$"  # YYYY-MM, years 2000 to 2099


def _filters(
    date_from: date, date_to: date, merchant: Merchant | None, product_id: uuid.UUID | None
) -> analytics.Filters:
    if date_to < date_from:
        raise ApiError(
            422,
            "validation_error",
            "Intervalo inválido.",
            [FieldError(field="to", message="Tem de ser ≥ from.")],
        )
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise ApiError(
            422,
            "validation_error",
            "Intervalo demasiado longo.",
            [FieldError(field="from", message="Máximo 3 anos.")],
        )
    return analytics.Filters(date_from, date_to, merchant.value if merchant else None, product_id)


@router.get(
    "/analytics/summary", response_model=SummaryOut, summary="Dashboard cards, time series and rankings"
)
def summary(
    db: Db,
    settings: SettingsDep,
    user: ConsentedUser,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    merchant: Merchant | None = None,
    product_id: uuid.UUID | None = None,
) -> SummaryOut:
    f = _filters(date_from, date_to, merchant, product_id)
    metrics.ANALYTICS_REQUESTS.labels("summary", str(merchant is not None), str(product_id is not None)).inc()
    return SummaryOut.model_validate(analytics.summary(db, user.id, f, settings.timezone))


@router.get(
    "/analytics/overview",
    response_model=OverviewOut,
    summary="Home: goal ring, monthly average, weeks, stores and latest receipts",
)
def overview(
    db: Db,
    settings: SettingsDep,
    config: AppConfigDep,
    user: ConsentedUser,
    today: Today,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-10"])] = None,
) -> OverviewOut:
    first = date.fromisoformat(f"{month}-01") if month else today.replace(day=1)
    metrics.ANALYTICS_REQUESTS.labels("overview", "False", "False").inc()
    return OverviewOut.model_validate(
        analytics.overview(db, user.id, first, today, settings.timezone, config)
    )


@router.get(
    "/analytics/months",
    response_model=list[MonthTotalOut],
    summary="Confirmed receipts and total spending per month, newest first",
)
def months(db: Db, settings: SettingsDep, user: ConsentedUser) -> list[MonthTotalOut]:
    metrics.ANALYTICS_REQUESTS.labels("months", "False", "False").inc()
    return [
        MonthTotalOut(
            month=f"{t.month.year:04d}-{t.month.month:02d}",
            confirmed_count=t.confirmed_count,
            total_cents=t.total_cents,
        )
        for t in analytics.month_totals(db, user.id, settings.timezone)
    ]


@router.get(
    "/analytics/consumption",
    response_model=ConsumptionOut,
    summary="Purchase frequency and monthly rate per product",
)
def consumption(
    db: Db,
    settings: SettingsDep,
    user: ConsentedUser,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    merchant: Merchant | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConsumptionOut:
    f = _filters(date_from, date_to, merchant, None)
    metrics.ANALYTICS_REQUESTS.labels("consumption", str(merchant is not None), "False").inc()
    return ConsumptionOut.model_validate(analytics.consumption(db, user.id, f, settings.timezone, limit))


@router.get(
    "/products", response_model=list[ProductSearchItem], summary="Search the user's purchased products"
)
def search_products(
    db: Db,
    user: ConsentedUser,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[ProductSearchItem]:
    return [ProductSearchItem.model_validate(p) for p in analytics.search_products(db, user.id, q, limit)]


@router.get(
    "/products/{product_id}/analytics",
    response_model=ProductAnalyticsOut,
    summary="Product prices and history",
)
def product_analytics(
    product_id: uuid.UUID,
    db: Db,
    settings: SettingsDep,
    user: ConsentedUser,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    merchant: Merchant | None = None,
) -> ProductAnalyticsOut:
    product = get_visible(db, user.id, product_id)
    if product is None:
        raise ApiError(404, "not_found", "Produto não encontrado.")
    f = _filters(date_from, date_to, merchant, product_id) if date_from and date_to else None
    metrics.ANALYTICS_REQUESTS.labels("product", str(merchant is not None), "True").inc()
    return ProductAnalyticsOut.model_validate(
        analytics.product_analytics(db, user.id, product, f, settings.timezone)
    )
