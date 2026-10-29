"""receipt_fiscal_qr

Revision ID: d4f7a2c8e1b6
Revises: c3e6a9d2f1b5
Create Date: 2026-10-25 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f7a2c8e1b6"
down_revision: str | None = "c3e6a9d2f1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The fiscal QR code (Portaria 195/2020): only what the checks use, never the buyer's tax number.
def _columns() -> list[sa.Column[object]]:
    return [
        sa.Column("issuer_nif", sa.String(9)),
        sa.Column("document_type", sa.String(4)),
        sa.Column("document_number", sa.String(64)),
        sa.Column("atcud", sa.String(70)),
        sa.Column("qr_date", sa.Date()),
        sa.Column("qr_total_cents", sa.BigInteger()),
        sa.Column("qr_tax_cents", sa.BigInteger()),
        sa.Column("qr_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column(
            "field_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
    ]


def upgrade() -> None:
    for column in _columns():
        op.add_column("receipts", column)
    op.create_index("ix_receipts_user_fiscal_key", "receipts", ["user_id", "issuer_nif", "document_number"])


def downgrade() -> None:
    op.drop_index("ix_receipts_user_fiscal_key", table_name="receipts")
    for column in reversed(_columns()):
        op.drop_column("receipts", column.name)
