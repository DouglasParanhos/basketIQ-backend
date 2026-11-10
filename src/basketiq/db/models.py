from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from basketiq.domain.status import (
    HOUSEHOLD_SIZE_RANGE,
    MONTHLY_GOAL_CENTS_RANGE,
    JobStatus,
    LineReviewStatus,
    ReceiptStatus,
    UserRole,
)


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    google_subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=UserRole.USER)
    status: Mapped[str] = mapped_column(String(16), default="active")
    privacy_accepted_at: Mapped[datetime | None]
    privacy_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[datetime | None]


class UserProfile(Base):
    """Optional personal profile (RF-13): household, usual merchants, goals and a monthly goal.

    One row per user, created on the first update. Deleted with the user.
    """

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "household_size BETWEEN {} AND {}".format(*HOUSEHOLD_SIZE_RANGE),
            name="ck_user_profiles_household_size",
        ),
        CheckConstraint(
            "monthly_goal_cents BETWEEN {} AND {}".format(*MONTHLY_GOAL_CENTS_RANGE),
            name="ck_user_profiles_monthly_goal",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    household_size: Mapped[int | None] = mapped_column(SmallInteger)
    merchants: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    goals: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    monthly_goal_cents: Mapped[int | None] = mapped_column(BigInteger)
    onboarding_completed_at: Mapped[datetime | None]
    passkey_offer_dismissed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class WebAuthnCredential(Base):
    """A passkey (RF-01, spec 016): public key and signature counter. Biometrics never leave the phone."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger)  # unsigned 32-bit on the authenticator
    transports: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None]


class WebAuthnChallenge(Base):
    """A one-time WebAuthn challenge. Deleted when used, so it cannot be replayed."""

    __tablename__ = "webauthn_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    challenge: Mapped[bytes] = mapped_column(LargeBinary)
    purpose: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(index=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    rotated_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        Index("ix_receipts_user_purchase", "user_id", "purchase_at"),
        Index("ix_receipts_user_merchant_purchase", "user_id", "merchant", "purchase_at"),
        Index("ix_receipts_merchant_purchase", "merchant", "purchase_at"),
        Index("ix_receipts_user_hash", "user_id", "file_hash"),
        Index("ix_receipts_user_fiscal_key", "user_id", "issuer_nif", "document_number"),
        Index("ix_receipts_original_expires", "original_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    merchant: Mapped[str | None] = mapped_column(String(32))
    merchant_hint: Mapped[str | None] = mapped_column(String(32))
    # Name of a store without a parser, as printed on the receipt or typed by the user.
    merchant_name: Mapped[str | None] = mapped_column(String(80))
    purchase_at: Mapped[datetime | None]
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    receipt_ref: Mapped[str | None] = mapped_column(String(128))
    store_label: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(8))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    object_key: Mapped[str] = mapped_column(String(512))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    declared_hash: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(32), default=ReceiptStatus.RECEIVED)
    error_code: Mapped[str | None] = mapped_column(String(64))
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    review_reasons: Mapped[list[Any]] = mapped_column(default=list)
    quality_warnings: Mapped[list[Any]] = mapped_column(default=list)
    manually_edited_fields: Mapped[list[Any]] = mapped_column(default=list)
    subtotal_cents: Mapped[int | None] = mapped_column(BigInteger)
    tax_cents: Mapped[int | None] = mapped_column(BigInteger)
    total_cents: Mapped[int | None] = mapped_column(BigInteger)
    duplicate_of_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="SET NULL")
    )
    duplicate_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None]
    confirmed_by: Mapped[str | None] = mapped_column(String(8))
    original_expires_at: Mapped[datetime | None]
    original_deleted_at: Mapped[datetime | None]
    # How the photos of a long receipt were joined: one {"photo", "overlap", "removed"} per pair of photos.
    merge_notes: Mapped[list[Any]] = mapped_column(default=list)
    # The fiscal QR code (Portaria 195/2020), only what the checks use: never the buyer's tax number (field B)
    # and never the raw text.
    issuer_nif: Mapped[str | None] = mapped_column(String(9))
    document_type: Mapped[str | None] = mapped_column(String(4))
    document_number: Mapped[str | None] = mapped_column(String(64))
    atcud: Mapped[str | None] = mapped_column(String(70))
    qr_date: Mapped[date | None]
    qr_total_cents: Mapped[int | None] = mapped_column(BigInteger)
    qr_tax_cents: Mapped[int | None] = mapped_column(BigInteger)
    # What the QR said against what was read, to review; and where a value came from when it was not the text.
    qr_notes: Mapped[list[Any]] = mapped_column(default=list)
    field_sources: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    lines: Mapped[list[ReceiptLine]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        order_by="ReceiptLine.line_number",
    )
    # The photos after the first one (which is `object_key`), for a receipt too long for a single photo.
    photos: Mapped[list[ReceiptPhoto]] = relationship(
        cascade="all, delete-orphan", order_by="ReceiptPhoto.position"
    )


class ReceiptPhoto(Base):
    __tablename__ = "receipt_photos"
    __table_args__ = (UniqueConstraint("receipt_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1 is the second photo
    object_key: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    declared_hash: Mapped[str | None] = mapped_column(String(64))


class ReceiptLine(Base):
    __tablename__ = "receipt_lines"
    __table_args__ = (Index("ix_receipt_lines_product", "normalized_product_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    raw_description: Mapped[str] = mapped_column(Text)
    normalized_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    quantity_decimal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(4))
    unit_price_cents: Mapped[int | None] = mapped_column(BigInteger)
    line_total_cents: Mapped[int | None] = mapped_column(BigInteger)
    discount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_code: Mapped[str | None] = mapped_column(String(4))
    confidence: Mapped[float | None] = mapped_column(Float)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    review_status: Mapped[str] = mapped_column(String(16), default=LineReviewStatus.OK)
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_fields: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    receipt: Mapped[Receipt] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        # Automatic (global) products are unique by key; user-created ones are unique per owner.
        Index(
            "uq_products_global_key",
            "normalized_key",
            unique=True,
            postgresql_where=text("owner_user_id IS NULL"),
        ),
        Index(
            "uq_products_owner_key",
            "owner_user_id",
            "normalized_key",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    canonical_name: Mapped[str] = mapped_column(String(255))
    normalized_key: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(64))  # not used in the MVP
    unit_type: Mapped[str | None] = mapped_column(String(8))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProductAlias(Base):
    """User-scoped mapping from a raw description key to the product the user chose."""

    __tablename__ = "product_aliases"
    __table_args__ = (UniqueConstraint("user_id", "raw_key", name="uq_product_alias_user_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    raw_key: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserLineRule(Base):
    """What one user decided for a (chain, normalised description) in «Por completar» (step C, D13).

    Private to the user and never fed into the catalogue; it beats the catalogue for that user's lines,
    past and future, so applying or removing it is retroactive without touching the receipts.
    """

    __tablename__ = "user_line_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "chain", "description_key", name="uq_user_line_rule"),
        CheckConstraint("NOT (not_comparable AND group_slug IS NOT NULL)", name="ck_user_line_rule_group"),
        CheckConstraint("(size_value IS NULL) = (size_unit IS NULL)", name="ck_user_line_rule_size"),
    )
    __mapper_args__ = {"eager_defaults": True}  # dates come back with the INSERT, not a second query  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chain: Mapped[str] = mapped_column(String(32))  # Receipt.merchant, "" when the receipt has none
    description_key: Mapped[str] = mapped_column(String(255))  # normalize_key(raw_description)
    group_slug: Mapped[str | None] = mapped_column(String(64))
    not_comparable: Mapped[bool] = mapped_column(Boolean, default=False)
    size_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    size_unit: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_processing_jobs_status", "status"),
        Index("ix_processing_jobs_external_month", "external_provider", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="SET NULL"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(32), default="EXTRACT")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    processing_route: Mapped[str | None] = mapped_column(String(32))
    ocr_engine: Mapped[str | None] = mapped_column(String(64))
    external_provider: Mapped[str | None] = mapped_column(String(64))
    external_reason: Mapped[str | None] = mapped_column(String(64))
    external_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    estimated_cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str | None] = mapped_column(String(8))
    merchant: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    model_version: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    stage_durations_ms: Mapped[dict[str, Any]] = mapped_column(default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    # Not a FK: audit rows outlive account deletion and keep only a pseudonymous reference.
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    receipt_id: Mapped[uuid.UUID | None]
    actor_type: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class MerchantConfig(Base):
    """Operator-managed parsing rules per merchant (thresholds and tolerances)."""

    __tablename__ = "merchant_configs"

    merchant: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    field_thresholds: Mapped[dict[str, Any]] = mapped_column(default=dict)
    default_threshold: Mapped[float | None] = mapped_column(Float)
    reconciliation_tolerance_cents: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[uuid.UUID | None]


class ShoppingList(Base):
    """The user's shopping list (one per user for now, with room for more). Deleted with the user."""

    __tablename__ = "shopping_lists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    merchant: Mapped[str | None] = mapped_column(String(32))  # the supermarket chosen on the list
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    items: Mapped[list[ShoppingListItem]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan"
    )


class ShoppingListItem(Base):
    """One product on the list. `deleted_at` is the «Desfazer» window: hidden first, purged later."""

    __tablename__ = "shopping_list_items"
    __table_args__ = (Index("ix_shopping_list_items_list_position", "list_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_lists.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal(1))
    unit: Mapped[str | None] = mapped_column(String(4))
    checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    already_have: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    position: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
