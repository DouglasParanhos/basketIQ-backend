"""existing_accounts_onboarding_done

Revision ID: f6b9c4e2a3d8
Revises: e5a8b3d1f2c7
Create Date: 2026-10-31 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f6b9c4e2a3d8"
down_revision: str | None = "e5a8b3d1f2c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The onboarding is for new accounts only (RF-14). Accounts that already accepted the privacy policy
    # are marked as done, with no profile value (constitution III): they set the goal in Settings.
    # An account that never consented has not reached the app yet and goes through the onboarding.
    op.execute(
        """
        INSERT INTO user_profiles (user_id, onboarding_completed_at)
        SELECT id, now() FROM users WHERE privacy_accepted_at IS NOT NULL
        ON CONFLICT (user_id) DO UPDATE SET onboarding_completed_at = now()
        WHERE user_profiles.onboarding_completed_at IS NULL
        """
    )


def downgrade() -> None:
    # Nothing to undo: a row made here cannot be told from one the person completed, and the dropping of
    # the table is the downgrade of e5a8b3d1f2c7.
    pass
