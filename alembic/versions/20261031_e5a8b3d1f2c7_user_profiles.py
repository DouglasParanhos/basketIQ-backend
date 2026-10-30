"""user_profiles

Revision ID: e5a8b3d1f2c7
Revises: d4f7a2c8e1b6
Create Date: 2026-10-31 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a8b3d1f2c7"
down_revision: str | None = "d4f7a2c8e1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One optional row per user (RF-13). Deleted with the user: it holds personal data of its own.
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("household_size", sa.SmallInteger(), nullable=True),
        sa.Column("merchants", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("goals", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("monthly_goal_cents", sa.BigInteger(), nullable=True),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("household_size BETWEEN 1 AND 12", name="ck_user_profiles_household_size"),
        sa.CheckConstraint("monthly_goal_cents BETWEEN 5000 AND 80000", name="ck_user_profiles_monthly_goal"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
