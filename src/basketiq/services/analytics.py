"""Aggregations over *confirmed* receipts only (RF-09, RF-10).

Spending is the sum of line nets (line total − discount), so manual corrections are reflected and
filtering by product uses the same basis as the unfiltered dashboard. Aggregates are computed by
query, which makes them idempotent: deleting or re-confirming a receipt can never double count.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.orm import Session

from basketiq.app_config import AppConfig
from basketiq.db.models import Product, Receipt, ReceiptLine, UserProfile
from basketiq.domain.normalization import normalize_key
from basketiq.domain.overview import (
    WEEK_DAYS,
    average_trend,
    goal_pct,
    monthly_average,
    recent_vs_average,
    ring_band,
    shares,
    week_blocks,
)
from basketiq.domain.status import MERCHANT_NAMES, SUPPORTED_MERCHANTS, Merchant, ReceiptStatus, Unit

UNIDENTIFIED = "Produto não identificado"


@dataclass
class Filters:
    date_from: date
    date_to: date  # inclusive
    merchant: str | None = None
    product_id: uuid.UUID | None = None

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1


def _bounds(f: Filters, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return datetime.combine(f.date_from, time(0), tz), datetime.combine(
        f.date_to + timedelta(days=1), time(0), tz
    )


def previous_period(f: Filters) -> Filters:
    span = f.days
    return Filters(
        f.date_from - timedelta(days=span), f.date_from - timedelta(days=1), f.merchant, f.product_id
    )


_net = ReceiptLine.line_total_cents - func.coalesce(ReceiptLine.discount_cents, 0)
# Weighed items count as one article; counted items by their quantity.
_units = case((ReceiptLine.unit == Unit.UNIT, ReceiptLine.quantity_decimal), else_=literal(1))


def _line_scope(stmt: Any, user_id: uuid.UUID, f: Filters, tz: ZoneInfo) -> Any:
    """Restrict a select over lines to the user's confirmed receipts matching the filters."""
    start, end = _bounds(f, tz)
    stmt = (
        stmt.select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            Receipt.purchase_at >= start,
            Receipt.purchase_at < end,
            ReceiptLine.line_total_cents.is_not(None),
        )
    )
    if f.merchant:
        stmt = stmt.where(Receipt.merchant == f.merchant)
    if f.product_id:
        stmt = stmt.where(ReceiptLine.normalized_product_id == f.product_id)
    return stmt


def _cards(db: Session, user_id: uuid.UUID, f: Filters, tz: ZoneInfo) -> dict[str, Any]:
    row = db.execute(
        _line_scope(
            select(
                func.coalesce(func.sum(_net), 0),
                func.count(func.distinct(Receipt.id)),
                func.coalesce(func.sum(_units), 0),
                func.count(ReceiptLine.id),
            ),
            user_id,
            f,
            tz,
        )
    ).one()
    spent, purchases, units, lines = row
    return {
        "total_spent_cents": int(spent),
        "purchases": int(purchases),
        "units": str(Decimal(units).normalize()) if units else "0",
        "articles": int(lines),
    }


def _variation(current: float | Decimal, previous: float | Decimal) -> float | None:
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100, 1)


def summary(db: Session, user_id: uuid.UUID, f: Filters, tz_name: str) -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    cards = _cards(db, user_id, f, tz)
    prev = previous_period(f)
    prev_cards = _cards(db, user_id, prev, tz)
    cards["variation_pct"] = {
        "total_spent": _variation(cards["total_spent_cents"], prev_cards["total_spent_cents"]),
        "purchases": _variation(cards["purchases"], prev_cards["purchases"]),
        "units": _variation(Decimal(cards["units"]), Decimal(prev_cards["units"])),
    }
    cards["previous_period"] = {
        "from": prev.date_from.isoformat(),
        "to": prev.date_to.isoformat(),
        **{k: prev_cards[k] for k in ("total_spent_cents", "purchases", "units")},
    }

    span = f.days
    granularity = "day" if span <= 62 else "month"
    local = func.timezone(tz_name, Receipt.purchase_at)
    bucket = func.date_trunc(granularity, local)
    series_rows = db.execute(
        _line_scope(select(bucket.label("b"), func.sum(_net)), user_id, f, tz).group_by("b").order_by("b")
    ).all()
    series = _fill_series({r[0].date(): int(r[1]) for r in series_rows}, f, granularity)

    merchant_rows = db.execute(
        _line_scope(
            select(Receipt.merchant, func.sum(_net), func.count(func.distinct(Receipt.id))), user_id, f, tz
        )
        .group_by(Receipt.merchant)
        .order_by(func.sum(_net).desc())
    ).all()
    total = cards["total_spent_cents"] or 1
    by_merchant = [
        {
            "merchant": m,
            "spent_cents": int(s),
            "purchases": int(c),
            "share_pct": round(int(s) / total * 100, 1),
        }
        for m, s, c in merchant_rows
    ]

    product_stmt = (
        _line_scope(
            select(
                ReceiptLine.normalized_product_id,
                func.max(Product.canonical_name),
                func.sum(_net).label("spent"),
                func.sum(_units).label("units"),
                func.count(ReceiptLine.id),
            ),
            user_id,
            f,
            tz,
        )
        .outerjoin(Product, Product.id == ReceiptLine.normalized_product_id)
        .group_by(ReceiptLine.normalized_product_id)
    )
    top_value = db.execute(product_stmt.order_by(func.sum(_net).desc()).limit(10)).all()
    top_qty = db.execute(
        product_stmt.order_by(func.sum(_units).desc(), func.sum(_net).desc()).limit(10)
    ).all()

    def product_row(r: Any) -> dict[str, Any]:
        return {
            "product_id": str(r[0]) if r[0] else None,
            "name": r[1] or UNIDENTIFIED,
            "spent_cents": int(r[2]),
            "units": str(Decimal(r[3]).normalize()),
            "lines": int(r[4]),
        }

    non_zero = sum(1 for p in series if p["spent_cents"])
    return {
        "filters": {
            "from": f.date_from.isoformat(),
            "to": f.date_to.isoformat(),
            "merchant": f.merchant,
            "product_id": str(f.product_id) if f.product_id else None,
        },
        "cards": cards,
        "series": {"granularity": granularity, "points": series, "insufficient_data": non_zero < 2},
        "by_merchant": by_merchant,
        "top_products_by_value": [product_row(r) for r in top_value],
        "top_products_by_quantity": [product_row(r) for r in top_qty],
        "empty": cards["purchases"] == 0,
    }


def _fill_series(values: dict[date, int], f: Filters, granularity: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    if granularity == "day":
        d = f.date_from
        while d <= f.date_to:
            points.append({"period": d.isoformat(), "spent_cents": values.get(d, 0)})
            d += timedelta(days=1)
    else:
        d = f.date_from.replace(day=1)
        while d <= f.date_to:
            points.append({"period": d.isoformat()[:7], "spent_cents": values.get(d, 0)})
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return points


def _base_price(
    unit_price_cents: int | None, unit: str | None, qty: Decimal | None, net: int
) -> tuple[int | None, str]:
    """Price per base unit (un, kg or l). g/ml lines are converted to kg/l."""
    basis = Unit.UNIT if unit in (None, Unit.UNIT) else (Unit.KG if unit in (Unit.KG, Unit.G) else Unit.L)
    if unit in (Unit.G, Unit.ML):
        if qty:
            return round(Decimal(net) / (qty / 1000)), basis
        return None, basis
    if unit_price_cents is not None:
        return unit_price_cents, basis
    if qty:
        return round(Decimal(net) / qty), basis
    return None, basis


def _priced_line(
    qty: Decimal | None, unit: str | None, unit_price: int | None, total: int | None, discount: int | None
) -> dict[str, Any]:
    """One purchase line with its net value and its price in the base unit of the line."""
    net = int(total or 0) - int(discount or 0)
    price, basis = _base_price(unit_price, unit, qty, net)
    return {
        "quantity": str(qty) if qty is not None else None,
        "unit": unit,
        "unit_price_cents": price,
        "price_basis": basis,
        "net_cents": net,
    }


def _qty_in_basis(h: dict[str, Any]) -> Decimal:
    q = Decimal(h["quantity"]) if h["quantity"] else Decimal(0)
    return q / 1000 if h["unit"] in (Unit.G, Unit.ML) else q


def _price_basis(history: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], Decimal]:
    """The most frequent base unit of the lines, the lines in it and their total quantity."""
    counter = Counter(h["price_basis"] for h in history)
    basis = counter.most_common(1)[0][0] if counter else Unit.UNIT
    same_basis = [h for h in history if h["price_basis"] == basis]
    return basis, same_basis, sum((_qty_in_basis(h) for h in same_basis), Decimal(0))


def _weighted_average(same_basis: list[dict[str, Any]], total_qty: Decimal) -> int | None:
    """Weighted by quantity: Σ net / Σ quantity, in the stated basis (spec: "base de cálculo")."""
    return round(Decimal(sum(h["net_cents"] for h in same_basis)) / total_qty) if total_qty else None


def product_analytics(
    db: Session, user_id: uuid.UUID, product: Product, f: Filters | None, tz_name: str
) -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    stmt = (
        select(
            Receipt.id,
            Receipt.purchase_at,
            Receipt.merchant,
            ReceiptLine.quantity_decimal,
            ReceiptLine.unit,
            ReceiptLine.unit_price_cents,
            ReceiptLine.line_total_cents,
            ReceiptLine.discount_cents,
            ReceiptLine.raw_description,
        )
        .select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            and_(
                Receipt.user_id == user_id,
                Receipt.processing_status == ReceiptStatus.CONFIRMED,
                ReceiptLine.normalized_product_id == product.id,
                ReceiptLine.line_total_cents.is_not(None),
            )
        )
        .order_by(Receipt.purchase_at)
    )
    if f is not None:
        start, end = _bounds(f, tz)
        stmt = stmt.where(Receipt.purchase_at >= start, Receipt.purchase_at < end)
        if f.merchant:
            stmt = stmt.where(Receipt.merchant == f.merchant)
    rows = db.execute(stmt).all()

    history: list[dict[str, Any]] = [
        {
            "receipt_id": str(rid),
            "date": purchase_at.astimezone(tz).date().isoformat() if purchase_at else None,
            "merchant": merchant,
            **_priced_line(qty, unit, unit_price, total, discount),
            "raw_description": raw,
        }
        for rid, purchase_at, merchant, qty, unit, unit_price, total, discount, raw in rows
    ]
    basis, same_basis, total_qty = _price_basis(history)
    total_spent = sum(h["net_cents"] for h in history)
    prices = [h["unit_price_cents"] for h in same_basis if h["unit_price_cents"] is not None]

    per_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in same_basis:
        per_merchant[h["merchant"] or "UNKNOWN"].append(h)
    comparison = []
    for merchant, items in sorted(per_merchant.items()):
        q = sum((_qty_in_basis(h) for h in items), Decimal(0))
        ps = [h["unit_price_cents"] for h in items if h["unit_price_cents"] is not None]
        comparison.append(
            {
                "merchant": merchant,
                "purchases": len(items),
                "avg_price_cents": _weighted_average(items, q),
                "min_price_cents": min(ps) if ps else None,
                "max_price_cents": max(ps) if ps else None,
                "last_price_cents": items[-1]["unit_price_cents"],
                "last_date": items[-1]["date"],
            }
        )

    return {
        "product": {"id": str(product.id), "name": product.canonical_name, "unit_type": product.unit_type},
        "price_basis": basis,
        "mixed_units": len({h["price_basis"] for h in history}) > 1,
        "total_quantity": str(total_qty.normalize()) if total_qty else "0",
        "total_spent_cents": total_spent,
        "avg_price_cents": _weighted_average(same_basis, total_qty),
        "min_price_cents": min(prices) if prices else None,
        "max_price_cents": max(prices) if prices else None,
        "purchases": len(history),
        "history": history,
        "by_merchant": comparison,
        "insufficient_data": len(history) < 2,
    }


def consumption(db: Session, user_id: uuid.UUID, f: Filters, tz_name: str, limit: int) -> dict[str, Any]:
    """How often and how much of each identified product the user buys in the period.

    ``purchases`` counts receipts; the interval is measured between distinct purchase days. Units use
    the dashboard rule (weighed items count as one article), so the rate is comparable with it.
    """
    tz = ZoneInfo(tz_name)
    rows = db.execute(
        _line_scope(
            select(
                ReceiptLine.normalized_product_id,
                func.max(Product.canonical_name),
                Receipt.id,
                Receipt.purchase_at,
                func.sum(_units),
                func.sum(_net),
            ),
            user_id,
            f,
            tz,
        )
        .outerjoin(Product, Product.id == ReceiptLine.normalized_product_id)
        .where(ReceiptLine.normalized_product_id.is_not(None))
        .group_by(ReceiptLine.normalized_product_id, Receipt.id, Receipt.purchase_at)
    ).all()

    per_product: dict[uuid.UUID, dict[str, Any]] = {}
    for pid, name, _rid, purchase_at, units, spent in rows:
        p = per_product.setdefault(
            pid, {"name": name, "purchases": 0, "units": Decimal(0), "spent": 0, "days": set()}
        )
        p["purchases"] += 1
        p["units"] += Decimal(units)
        p["spent"] += int(spent)
        p["days"].add(purchase_at.astimezone(tz).date())

    months = Decimal(f.days) / Decimal("30.4375")
    items = []
    for pid, p in per_product.items():
        days = sorted(p["days"])
        interval = round((days[-1] - days[0]).days / (len(days) - 1), 1) if len(days) > 1 else None
        items.append(
            {
                "product_id": str(pid),
                "name": p["name"],
                "purchases": p["purchases"],
                "units": str(p["units"].normalize()),
                "units_per_month": round(float(p["units"] / months), 1),
                "spent_cents": p["spent"],
                "avg_interval_days": interval,
                "first_purchase": days[0].isoformat(),
                "last_purchase": days[-1].isoformat(),
            }
        )
    items.sort(key=lambda i: (-i["purchases"], -Decimal(i["units"]), i["name"] or ""))
    return {
        "filters": {
            "from": f.date_from.isoformat(),
            "to": f.date_to.isoformat(),
            "merchant": f.merchant,
            "product_id": None,
        },
        "items": items[:limit],
        "empty": not items,
    }


def search_products(db: Session, user_id: uuid.UUID, q: str | None, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            Product.id,
            Product.canonical_name,
            Product.unit_type,
            func.count(ReceiptLine.id),
            func.coalesce(func.sum(_net), 0),
            func.max(Receipt.purchase_at),
        )
        .select_from(Product)
        .join(ReceiptLine, ReceiptLine.normalized_product_id == Product.id)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(Receipt.user_id == user_id, Receipt.processing_status == ReceiptStatus.CONFIRMED)
        .group_by(Product.id)
        .order_by(func.sum(_net).desc())
        .limit(limit)
    )
    if q:
        # normalize_key keeps only [A-Z0-9 %]; drop "%" so user input cannot inject wildcards.
        term = normalize_key(q).replace("%", "")
        stmt = stmt.where(Product.normalized_key.like(f"%{term}%"))
    found = db.execute(stmt).all()
    if not found:
        return []

    # One query for the lines of the chosen products, priced with the same functions as the detail view.
    line_rows = db.execute(
        select(
            ReceiptLine.normalized_product_id,
            Receipt.id,
            Receipt.merchant,
            ReceiptLine.quantity_decimal,
            ReceiptLine.unit,
            ReceiptLine.unit_price_cents,
            ReceiptLine.line_total_cents,
            ReceiptLine.discount_cents,
        )
        .select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            ReceiptLine.normalized_product_id.in_([r[0] for r in found]),
            ReceiptLine.line_total_cents.is_not(None),
        )
    ).all()
    lines_of: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for pid, rid, merchant, qty, unit, unit_price, total, discount in line_rows:
        lines_of[pid].append(
            {"receipt_id": rid, "merchant": merchant, **_priced_line(qty, unit, unit_price, total, discount)}
        )

    items = []
    for pid, name, unit_type, line_count, spent, last in found:
        history = lines_of[pid]
        basis, same_basis, total_qty = _price_basis(history)
        items.append(
            {
                "id": str(pid),
                "name": name,
                "unit_type": unit_type,
                "lines": int(line_count or 0),
                "spent_cents": int(spent or 0),
                "last_purchase_at": last.isoformat() if last else None,
                "avg_price_cents": _weighted_average(same_basis, total_qty),
                "price_basis": basis,
                "purchases": len({h["receipt_id"] for h in history}),
                "merchants": len({h["merchant"] for h in history if h["merchant"] in SUPPORTED_MERCHANTS}),
            }
        )
    return items


# ---- Home overview and month totals (RF-15, spec 014) -----------------------------------------------------

# The recent-receipts list also shows what still needs the person's attention; failed receipts stay out.
RECENT_STATUSES = (
    ReceiptStatus.CONFIRMED,
    ReceiptStatus.REVIEW_REQUIRED,
    ReceiptStatus.COMPLETED,
    ReceiptStatus.RECEIVED,
    ReceiptStatus.VALIDATING,
    ReceiptStatus.EXTRACTING,
)
RECENT_LIMIT = 5
OTHER_MERCHANTS = "Outros"

# What a receipt cost: its printed total or, when it has none, the net of its lines (spec 014 R1).
_line_net = (
    select(func.sum(_net))
    .where(ReceiptLine.receipt_id == Receipt.id, ReceiptLine.line_total_cents.is_not(None))
    .correlate(Receipt)
    .scalar_subquery()
)
_spend = func.coalesce(Receipt.total_cents, _line_net, 0)


class MonthTotal(NamedTuple):
    month: date  # first day of the month, in local time
    confirmed_count: int
    total_cents: int


def month_totals(db: Session, user_id: uuid.UUID, tz_name: str) -> list[MonthTotal]:
    """Confirmed receipts and spending per local month, newest month first; months without any are absent."""
    month = func.date_trunc("month", func.timezone(tz_name, Receipt.purchase_at))
    rows = db.execute(
        select(month, func.count(Receipt.id), func.sum(_spend))
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            Receipt.purchase_at.is_not(None),
        )
        .group_by(month)
        .order_by(month.desc())
    ).all()
    return [MonthTotal(m.date(), int(count), int(total)) for m, count, total in rows]


def merchant_label(merchant: str | None, merchant_name: str | None) -> tuple[str | None, str]:
    """(id, name) of a store: supported merchants by their catalogue name, any other by the name it has."""
    if merchant in SUPPORTED_MERCHANTS:
        return merchant, MERCHANT_NAMES[Merchant(merchant)]
    return None, merchant_name or OTHER_MERCHANTS


def overview(
    db: Session, user_id: uuid.UUID, month: date, today: date, tz_name: str, config: AppConfig
) -> dict[str, Any]:
    """The Home screen for `month` (its first day): goal ring, average, weeks, stores and latest receipts."""
    tz = ZoneInfo(tz_name)
    blocks = week_blocks(month.year, month.month)
    start, end = _bounds(Filters(month, blocks[-1][1]), tz)
    owned = Receipt.user_id == user_id

    spent_rows = db.execute(
        select(Receipt.purchase_at, Receipt.merchant, Receipt.merchant_name, _spend).where(
            owned,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            Receipt.purchase_at >= start,
            Receipt.purchase_at < end,
        )
    ).all()
    spent = sum(int(cents) for *_, cents in spent_rows)

    weeks = [0] * len(blocks)
    stores: dict[tuple[str | None, str], list[int]] = {}
    for purchase_at, merchant, merchant_name, cents in spent_rows:
        weeks[(purchase_at.astimezone(tz).day - 1) // WEEK_DAYS] += int(cents)
        count_and_total = stores.setdefault(merchant_label(merchant, merchant_name), [0, 0])
        count_and_total[0] += 1
        count_and_total[1] += int(cents)
    ranked = sorted(stores.items(), key=lambda kv: (-kv[1][1], kv[0][1]))
    percentages = shares([total for _, (_, total) in ranked])
    by_merchant = [
        {"merchant": key[0], "name": key[1], "spent_cents": total, "purchases": count, "share_pct": pct}
        for (key, (count, total)), pct in zip(ranked, percentages, strict=False)
    ]

    totals = month_totals(db, user_id, tz_name)
    counts = {t.month: t.confirmed_count for t in totals}
    complete = [t.total_cents for t in totals if t.month < today.replace(day=1)]
    average = monthly_average(complete, config.average_min_months)
    trend = recent_vs_average(complete) if average is not None else None

    profile = db.get(UserProfile, user_id)
    goal = profile.monthly_goal_cents if profile else None
    pct = goal_pct(spent, goal) if goal else None

    effective = func.coalesce(Receipt.purchase_at, Receipt.created_at)
    shown_total = case(
        (Receipt.processing_status == ReceiptStatus.CONFIRMED, _spend), else_=Receipt.total_cents
    )
    recent = db.execute(
        select(
            Receipt.id,
            Receipt.merchant,
            Receipt.merchant_name,
            effective,
            shown_total,
            Receipt.processing_status,
        )
        .where(
            owned,
            Receipt.processing_status.in_([s.value for s in RECENT_STATUSES]),
            effective >= start,
            effective < end,
        )
        .order_by(effective.desc(), Receipt.id.desc())
        .limit(RECENT_LIMIT)
    ).all()

    return {
        "month": f"{month.year:04d}-{month.month:02d}",
        "spent_cents": spent,
        "goal_cents": goal,
        "goal_pct": pct,
        "band": "none"
        if pct is None
        else ring_band(pct, config.goal_ring_green_up_to_pct, config.goal_ring_orange_up_to_pct),
        "monthly_average_cents": average,
        "recent_vs_average_pct": trend,
        "average_trend": average_trend(trend, config.average_on_target_pct),
        "average_months": len(complete),
        "average_min_months": config.average_min_months,
        "purchases_this_month": len(spent_rows),
        "purchases_last_month": counts.get((month - timedelta(days=1)).replace(day=1), 0),
        "weeks": [
            {"index": i, "from": first, "to": last, "spent_cents": cents}
            for i, ((first, last), cents) in enumerate(zip(blocks, weeks, strict=True), start=1)
        ],
        "by_merchant": by_merchant,
        "recent_receipts": [
            {
                "id": rid,
                "merchant": (label := merchant_label(merchant, merchant_name))[0],
                "name": label[1],
                "date": when.astimezone(tz).date(),
                "total_cents": None if total is None else int(total),
                "status": status,
            }
            for rid, merchant, merchant_name, when, total, status in recent
        ],
        "empty": not totals,
    }
