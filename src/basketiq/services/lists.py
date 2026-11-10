"""The shopping list (RF-16): one list per user, its items, and the prices the person last paid.

Nothing here reads other people's data: every price comes from the user's own confirmed receipts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from basketiq.api.errors import not_found
from basketiq.db.models import Product, Receipt, ReceiptLine, ShoppingList, ShoppingListItem, UserProfile
from basketiq.domain.categories import CATEGORIES, CATEGORY_IDS, CATEGORY_NAMES, OTHER, categorize
from basketiq.domain.expected_price import PricePoint, Store, cost_cents, expected_price
from basketiq.domain.grouping import Catalog, classify
from basketiq.domain.normalization import normalize_key
from basketiq.domain.status import MERCHANT_NAMES, SUPPORTED_MERCHANTS, Merchant, ReceiptStatus
from basketiq.services import analytics, groups, products

UNDO_WINDOW = timedelta(
    days=1
)  # a removed item is kept this long, so «Desfazer» always has something to restore
SUGGESTIONS = 8


def get_list(db: Session, user_id: uuid.UUID) -> ShoppingList:
    """The user's list, created on first use (two first requests at once make one list)."""
    found = db.scalar(select(ShoppingList).where(ShoppingList.user_id == user_id))
    if found is not None:
        return found
    db.execute(
        pg_insert(ShoppingList)
        .values(id=uuid.uuid4(), user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    return db.execute(select(ShoppingList).where(ShoppingList.user_id == user_id)).scalar_one()


def selected_merchant(db: Session, lst: ShoppingList) -> str:
    """The supermarket of the list: the one chosen, else the first usual one of the person, else the first."""
    if lst.merchant in SUPPORTED_MERCHANTS:
        return str(lst.merchant)
    profile = db.get(UserProfile, lst.user_id)
    usual = [m for m in (profile.merchants if profile else []) if m in SUPPORTED_MERCHANTS]
    return str(usual[0] if usual else SUPPORTED_MERCHANTS[0])


def set_merchant(db: Session, user_id: uuid.UUID, merchant: Merchant) -> ShoppingList:
    lst = get_list(db, user_id)
    lst.merchant = merchant.value
    return lst


def last_prices(
    db: Session, user_id: uuid.UUID, product_ids: set[uuid.UUID], tz: ZoneInfo
) -> dict[uuid.UUID, dict[Store, PricePoint]]:
    """For each product, the last price paid at each store (net of discounts, per unit of the line)."""
    if not product_ids:
        return {}
    ranked = (
        select(
            ReceiptLine.normalized_product_id.label("pid"),
            Receipt.merchant,
            Receipt.merchant_name,
            Receipt.purchase_at,
            ReceiptLine.quantity_decimal.label("qty"),
            ReceiptLine.unit,
            ReceiptLine.line_total_cents.label("total"),
            ReceiptLine.discount_cents.label("discount"),
            func.row_number()
            .over(
                partition_by=(ReceiptLine.normalized_product_id, Receipt.merchant, Receipt.merchant_name),
                order_by=[Receipt.purchase_at.desc(), ReceiptLine.line_number.desc()],
            )
            .label("rn"),
        )
        .select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            Receipt.purchase_at.is_not(None),
            ReceiptLine.normalized_product_id.in_(product_ids),
            ReceiptLine.line_total_cents.is_not(None),
        )
        .subquery()
    )
    out: dict[uuid.UUID, dict[Store, PricePoint]] = {}
    for row in db.execute(select(ranked).where(ranked.c.rn == 1)):
        priced = analytics.priced_line(row.qty, row.unit, None, row.total, row.discount)
        if priced["unit_price_cents"] is None:
            continue
        store = analytics.merchant_label(row.merchant, row.merchant_name)
        point = PricePoint(
            priced["unit_price_cents"], priced["price_basis"], row.purchase_at.astimezone(tz).date(), *store
        )
        known = out.setdefault(row.pid, {}).get(store)
        if known is None or point.day > known.day:
            out[row.pid][store] = point
    return out


def _expected(selected: str | None, ladder: dict[Store, PricePoint]) -> dict[str, Any] | None:
    found = expected_price(selected, ladder)
    if found is None:
        return None
    point, other_merchant = found
    return {
        "price_cents": point.cents,
        "price_basis": point.basis,
        "date": point.day.isoformat(),
        "merchant": point.merchant,
        "merchant_name": point.merchant_name,
        "other_merchant": other_merchant,
        "source": "own_receipts",
    }


def _purge_removed(lst: ShoppingList) -> None:
    cutoff = datetime.now(UTC) - UNDO_WINDOW
    for item in [i for i in lst.items if i.deleted_at is not None and i.deleted_at < cutoff]:
        lst.items.remove(item)


def _active(lst: ShoppingList) -> list[ShoppingListItem]:
    order = {c: n for n, c in enumerate(CATEGORY_IDS)}
    items = [i for i in lst.items if i.deleted_at is None]
    return sorted(items, key=lambda i: (order.get(i.category, len(order)), i.position))


def view(db: Session, user_id: uuid.UUID, tz: ZoneInfo) -> dict[str, Any]:
    """The whole list, as the screen draws it: items in their categories, each with its price, and an estimate
    for every supermarket (an item counts unless it is «Já tenho» and not in the cart).
    """
    lst = get_list(db, user_id)
    _purge_removed(lst)
    items = _active(lst)
    selected = selected_merchant(db, lst)
    ladders = last_prices(db, user_id, {i.product_id for i in items if i.product_id}, tz)

    def ladder(item: ShoppingListItem) -> dict[Store, PricePoint]:
        return ladders.get(item.product_id, {}) if item.product_id else {}

    counted = [i for i in items if not i.already_have or i.checked]
    estimates = []
    for merchant in SUPPORTED_MERCHANTS:
        total, with_price, other = 0, 0, 0
        for item in counted:
            found = expected_price(merchant.value, ladder(item))
            if found is not None:
                point, from_other = found
                cost = cost_cents(item.quantity, item.unit, point)
                if cost is None:
                    continue  # the unit does not compare with the price's: left out, never guessed
                total += cost
                with_price += 1
                other += from_other
        estimates.append(
            {
                "merchant": merchant.value,
                "merchant_name": MERCHANT_NAMES[merchant],
                "total_cents": total,
                "items_with_price": with_price,
                "other_merchant_values": other,
            }
        )
    return {
        "merchant": selected,
        "categories": [{"id": c, "name": n} for c, n in CATEGORIES],
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "category": i.category,
                "category_name": CATEGORY_NAMES.get(i.category, CATEGORY_NAMES[OTHER]),
                "quantity": str(i.quantity.normalize()),
                "unit": i.unit,
                "checked": i.checked,
                "already_have": i.already_have,
                "product_id": i.product_id,
                "expected": _expected(selected, ladder(i)),
            }
            for i in items
        ],
        "estimates": estimates,
        "items_total": len(items),
        "checked_count": sum(1 for i in items if i.checked),
        "already_have_excluded": sum(1 for i in items if i.already_have and not i.checked),
    }


def _linked_product(db: Session, user_id: uuid.UUID, name: str) -> Product | None:
    """A product the person already bought under this name (the same matching key the receipts use)."""
    key = normalize_key(name)
    if not key:
        return None
    return db.scalar(
        select(Product)
        .where(Product.normalized_key == key, products.visible_to(user_id))
        .order_by(Product.owner_user_id.is_(None).desc())
        .limit(1)
    )


def _category(
    db: Session, user_id: uuid.UUID, catalog: Catalog, name: str, product_id: uuid.UUID | None
) -> str:
    """The list category of the product's step C group (with the person's own rules), else of what the
    catalogue makes of the name, else the keywords. Every category the list gives goes through here."""
    group = groups.product_group(db, user_id, product_id, catalog) if product_id else None
    if group is None:
        named = classify(name, None, catalog)
        group = named.group or named.hint
    found = catalog.nodes[group].list_category if group else None
    return found or categorize(name)


def add_item(
    db: Session,
    user_id: uuid.UUID,
    catalog: Catalog,
    *,
    name: str,
    quantity: Decimal,
    unit: str | None,
    category: str | None,
    already_have: bool,
    product_id: uuid.UUID | None,
) -> ShoppingListItem:
    lst = get_list(db, user_id)
    if product_id is None:
        product = _linked_product(db, user_id, name)
    elif (product := products.get_visible(db, user_id, product_id)) is None:
        raise not_found("Produto")
    last = db.scalar(select(func.max(ShoppingListItem.position)).where(ShoppingListItem.list_id == lst.id))
    item = ShoppingListItem(
        list_id=lst.id,
        product_id=product.id if product else None,
        name=name,
        category=category or _category(db, user_id, catalog, name, product.id if product else None),
        quantity=quantity,
        unit=unit,
        already_have=already_have,
        position=0 if last is None else last + 1,
    )
    db.add(item)
    db.flush()
    return item


def _item(db: Session, user_id: uuid.UUID, item_id: uuid.UUID, *, removed: bool = False) -> ShoppingListItem:
    lst = get_list(db, user_id)
    item = db.scalar(
        select(ShoppingListItem).where(ShoppingListItem.id == item_id, ShoppingListItem.list_id == lst.id)
    )
    if item is None or (item.deleted_at is not None) != removed:
        raise not_found("Item")
    return item


def update_item(
    db: Session, user_id: uuid.UUID, catalog: Catalog, item_id: uuid.UUID, changes: dict[str, Any]
) -> ShoppingListItem:
    item = _item(db, user_id, item_id)
    renamed = "name" in changes and changes["name"] != item.name
    for field, value in changes.items():
        if value is None and field in ("name", "quantity", "category", "checked", "already_have"):
            continue  # these cannot be cleared; only the unit can
        setattr(item, field, value)
    if renamed:
        product = _linked_product(db, user_id, item.name)
        item.product_id = product.id if product else None
        if "category" not in changes:
            item.category = _category(db, user_id, catalog, item.name, item.product_id)
    return item


def remove_item(db: Session, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
    _item(db, user_id, item_id).deleted_at = datetime.now(UTC)


def restore_item(db: Session, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Puts a removed item back where it was: its position was never given away."""
    _item(db, user_id, item_id, removed=True).deleted_at = None


def evolution(
    db: Session, user_id: uuid.UUID, item_id: uuid.UUID, months: int, today: date, tz: ZoneInfo
) -> dict[str, Any]:
    """The prices paid for a list item, at every supermarket, for the chart and the list of purchases."""
    item = _item(db, user_id, item_id)
    empty: dict[str, Any] = {
        "has_history": False,
        "price_basis": None,
        "expected": None,
        "last_cents": None,
        "min_cents": None,
        "max_cents": None,
        "points": [],
    }
    if item.product_id is None:
        return empty
    rows = db.execute(
        select(
            Receipt.purchase_at,
            Receipt.merchant,
            Receipt.merchant_name,
            ReceiptLine.quantity_decimal,
            ReceiptLine.unit,
            ReceiptLine.line_total_cents,
            ReceiptLine.discount_cents,
        )
        .select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            ReceiptLine.normalized_product_id == item.product_id,
            Receipt.purchase_at.is_not(None),
            ReceiptLine.line_total_cents.is_not(None),
        )
        .order_by(Receipt.purchase_at, ReceiptLine.line_number)
    ).all()
    history = []
    for purchase_at, merchant, merchant_name, qty, unit, total, discount in rows:
        priced = analytics.priced_line(qty, unit, None, total, discount)
        if priced["unit_price_cents"] is not None and purchase_at is not None:
            id_, name = analytics.merchant_label(merchant, merchant_name)
            history.append(
                {**priced, "day": purchase_at.astimezone(tz).date(), "merchant": id_, "merchant_name": name}
            )
    basis, same_basis, _ = analytics.price_basis(history)
    # The window is the chart's; when nothing was bought in it, the older purchases still say what was paid.
    start = today - timedelta(days=30 * months)
    points = [h for h in same_basis if h["day"] >= start] or same_basis
    if not points:
        return empty
    prices = [h["unit_price_cents"] for h in points]
    ladder = last_prices(db, user_id, {item.product_id}, tz).get(item.product_id, {})
    return {
        "has_history": True,
        "price_basis": basis,
        "expected": _expected(selected_merchant(db, item.shopping_list), ladder),
        "last_cents": prices[-1],
        "min_cents": min(prices),
        "max_cents": max(prices),
        "points": [
            {
                "date": h["day"].isoformat(),
                "price_cents": h["unit_price_cents"],
                "quantity": h["quantity"],
                "merchant": h["merchant"],
                "merchant_name": h["merchant_name"],
                "origin": "own",
            }
            for h in points
        ],
    }


def suggestions(
    db: Session, user_id: uuid.UUID, catalog: Catalog, today: date, window_days: int, tz_name: str
) -> list[dict[str, Any]]:
    """«Para a próxima compra»: what the person buys most often, minus what is already on the list."""
    lst = get_list(db, user_id)
    active = _active(lst)
    on_list = {i.product_id for i in active if i.product_id}
    on_list_keys = {normalize_key(i.name) for i in active}
    bought = analytics.list_articles(db, user_id, today, window_days, tz_name)["items"]
    usual = sorted(bought, key=lambda i: (-i["purchases"], i["name"]))
    out = []
    for article in usual:
        pid = uuid.UUID(article["product_id"])
        if pid in on_list or normalize_key(article["name"]) in on_list_keys:
            continue
        category = _category(db, user_id, catalog, article["name"], pid)
        out.append(
            {
                "product_id": pid,
                "name": article["name"],
                "category": category,
                "category_name": CATEGORY_NAMES[category],
            }
        )
        if len(out) == SUGGESTIONS:
            break
    return out
