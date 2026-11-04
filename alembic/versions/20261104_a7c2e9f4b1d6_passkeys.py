"""passkeys

Revision ID: a7c2e9f4b1d6
Revises: f6b9c4e2a3d8
Create Date: 2026-11-04 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c2e9f4b1d6"
down_revision: str | None = "f6b9c4e2a3d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Passkeys (RF-01, spec 016): only the public key, the signature counter and dates. Deleted with the user.
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("transports", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index("ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"])
    # One-time challenges, 5 minutes. Kept server side so a used one cannot be replayed.
    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webauthn_challenges_expires_at", "webauthn_challenges", ["expires_at"])
    op.add_column(
        "user_profiles", sa.Column("passkey_offer_dismissed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "passkey_offer_dismissed_at")
    op.drop_index("ix_webauthn_challenges_expires_at", table_name="webauthn_challenges")
    op.drop_table("webauthn_challenges")
    op.drop_index("ix_webauthn_credentials_user_id", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
