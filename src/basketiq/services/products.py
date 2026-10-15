"""Product normalisation and user corrections (RF-10, decisão MVP 6).

Automatic products are global (shared catalogue keyed by the normalised description). When a user
renames/reassigns a product, the correction is stored as a user-scoped alias and, if needed, a
user-owned product, so one user's corrections never change another user's data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, and_, exists, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from basketiq.db.models import Product, ProductAlias, Receipt, ReceiptLine
from basketiq.domain.normalization import canonical_name, normalize_key


def resolve_product(
    db: Session, user_id: uuid.UUID, raw_description: str, unit: str | None
) -> uuid.UUID | None:
    key = normalize_key(raw_description)
    if not key:
        return None
    alias = db.scalar(
        select(ProductAlias.product_id).where(ProductAlias.user_id == user_id, ProductAlias.raw_key == key)
    )
    if alias is not None:
        return alias
    return _global_product(db, key, canonical_name(raw_description), unit)


def _global_product(db: Session, key: str, name: str, unit: str | None) -> uuid.UUID:
    # Concurrent workers may create the same product: insert-or-ignore, then read.
    db.execute(
        pg_insert(Product)
        .values(id=uuid.uuid4(), canonical_name=name, normalized_key=key, unit_type=unit)
        .on_conflict_do_nothing(index_elements=["normalized_key"], index_where=text("owner_user_id IS NULL"))
    )
    product_id = db.scalar(
        select(Product.id).where(Product.normalized_key == key, Product.owner_user_id.is_(None))
    )
    assert product_id is not None
    return product_id


def assign_by_name(
    db: Session, user_id: uuid.UUID, raw_description: str, product_name: str, unit: str | None
) -> uuid.UUID:
    """User typed a product name for a line: reuse a visible product with that name or create a
    user-owned one, and remember the choice for this description."""
    name = canonical_name(product_name)
    key = normalize_key(name)
    product_id = db.scalar(
        select(Product.id)
        .where(Product.normalized_key == key, visible_to(user_id))
        .order_by(Product.owner_user_id.is_(None))  # prefer the user's own product
        .limit(1)
    )
    if product_id is None:
        product = Product(canonical_name=name, normalized_key=key, unit_type=unit, owner_user_id=user_id)
        db.add(product)
        db.flush()
        product_id = product.id
    remember_alias(db, user_id, raw_description, product_id)
    return product_id


def remember_alias(db: Session, user_id: uuid.UUID, raw_description: str, product_id: uuid.UUID) -> None:
    raw_key = normalize_key(raw_description)
    if not raw_key:
        return
    db.execute(
        pg_insert(ProductAlias)
        .values(id=uuid.uuid4(), user_id=user_id, raw_key=raw_key, product_id=product_id)
        .on_conflict_do_update(constraint="uq_product_alias_user_key", set_={"product_id": product_id})
    )


def visible_to(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Products a user may see: their own, or global ones present in their receipts."""
    used = exists().where(
        ReceiptLine.normalized_product_id == Product.id,
        ReceiptLine.receipt_id == Receipt.id,
        Receipt.user_id == user_id,
    )
    return or_(Product.owner_user_id == user_id, and_(Product.owner_user_id.is_(None), used))


def get_visible(db: Session, user_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    return db.scalar(select(Product).where(Product.id == product_id, visible_to(user_id)))
