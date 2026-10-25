"""receipt_store_label

Revision ID: a1c4e7b2d9f3
Revises: 54d0d18ee551
Create Date: 2026-10-24 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7b2d9f3"
down_revision: str | None = "54d0d18ee551"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("store_label", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("receipts", "store_label")
