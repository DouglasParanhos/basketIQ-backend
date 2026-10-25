"""receipt_photos

Revision ID: c3e6a9d2f1b5
Revises: b2d5f8c1e0a4
Create Date: 2026-10-24 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e6a9d2f1b5"
down_revision: str | None = "b2d5f8c1e0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column(
            "merge_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
    )
    op.create_table(
        "receipt_photos",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("receipt_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_id", "position"),
    )
    op.create_index("ix_receipt_photos_receipt_id", "receipt_photos", ["receipt_id"])


def downgrade() -> None:
    op.drop_index("ix_receipt_photos_receipt_id", table_name="receipt_photos")
    op.drop_table("receipt_photos")
    op.drop_column("receipts", "merge_notes")
