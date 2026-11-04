"""The optional personal profile (RF-13, RF-14): household, usual merchants, goals and a monthly goal.

Only what the person sent is stored: an omitted field is kept and a suggested value is never saved in
their name (constitution III). The row is created on the first update; without one the profile is empty.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from basketiq.db.models import UserProfile


def get_profile(db: Session, user_id: uuid.UUID) -> UserProfile | None:
    return db.get(UserProfile, user_id)


# A `true` flag in the patch sets its date the first time only; later ones keep that date.
SET_ONCE = {
    "onboarding_completed": "onboarding_completed_at",
    "passkey_offer_dismissed": "passkey_offer_dismissed_at",
}


def update_profile(db: Session, user_id: uuid.UUID, changes: dict[str, Any]) -> UserProfile:
    """Apply the fields present in `changes`; the SET_ONCE flags only set their date the first time."""
    # Create-then-lock: two simultaneous updates of one person neither duplicate the row nor lose a field.
    db.execute(insert(UserProfile).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"]))
    profile = db.scalars(select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()).one()
    flags = {flag: changes.pop(flag, False) for flag in SET_ONCE}
    for field, value in changes.items():
        setattr(profile, field, value)
    for flag, column in SET_ONCE.items():
        if flags[flag] and getattr(profile, column) is None:
            setattr(profile, column, datetime.now(UTC))
    db.flush()
    return profile
