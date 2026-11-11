"""user line rules (step C, «Por completar»)

Revision ID: d3e7a91c5f20
Revises: b8d3f1a5c7e2
Create Date: 2026-11-10 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e7a91c5f20"
down_revision: str | None = "b8d3f1a5c7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # What one user decided for a (chain, normalised description): group, size or «não comparável»
    # (spec 010, D13).
    # Private to the user, deleted with the account; the receipts themselves are never rewritten.
    op.create_table(
        "user_line_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("description_key", sa.String(length=255), nullable=False),
        sa.Column("group_slug", sa.String(length=64), nullable=True),
        sa.Column("not_comparable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("size_value", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("size_unit", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "chain", "description_key", name="uq_user_line_rule"),
        sa.CheckConstraint("NOT (not_comparable AND group_slug IS NOT NULL)", name="ck_user_line_rule_group"),
        sa.CheckConstraint("(size_value IS NULL) = (size_unit IS NULL)", name="ck_user_line_rule_size"),
    )
    op.create_index("ix_user_line_rules_user_id", "user_line_rules", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_line_rules_user_id", table_name="user_line_rules")
    op.drop_table("user_line_rules")
