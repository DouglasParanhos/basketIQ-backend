from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from basketiq.domain.status import JobStatus, LineReviewStatus, ReceiptStatus, UserRole


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
        Index("ix_receipts_original_expires", "original_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    merchant: Mapped[str | None] = mapped_column(String(32))
    merchant_hint: Mapped[str | None] = mapped_column(String(32))
    purchase_at: Mapped[datetime | None]
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    receipt_ref: Mapped[str | None] = mapped_column(String(128))
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
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    lines: Mapped[list[ReceiptLine]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        order_by="ReceiptLine.line_number",
    )


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
