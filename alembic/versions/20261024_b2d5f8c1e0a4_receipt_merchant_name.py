"""receipt_merchant_name

Revision ID: b2d5f8c1e0a4
Revises: a1c4e7b2d9f3
Create Date: 2026-10-24 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d5f8c1e0a4"
down_revision: str | None = "a1c4e7b2d9f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("merchant_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("receipts", "merchant_name")
