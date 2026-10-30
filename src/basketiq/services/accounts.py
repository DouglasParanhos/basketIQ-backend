from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from basketiq.auth.sessions import revoke_all_sessions
from basketiq.config import Settings
from basketiq.db.models import AuditEvent, Product, ProductAlias, Receipt, User
from basketiq.domain.status import ActorType, UserRole
from basketiq.services import audit
from basketiq.storage import ObjectStorage, user_prefix

PRIVACY_POLICY_VERSION = "2026-10-15"


def upsert_from_claims(db: Session, settings: Settings, claims: dict[str, Any]) -> tuple[User, bool]:
    """Create or recover the profile. Only the attributes needed to operate are kept (RF-01)."""
    subject = f"{settings.oidc_issuer}|{claims['sub']}"
    email = str(claims["email"]).lower()
    user = db.scalar(select(User).where(User.google_subject == subject))
    created = user is None
    if user is None:
        user = User(google_subject=subject, email=email)
        db.add(user)
    user.email = email
    name = claims.get("name") or claims.get("given_name")
    user.display_name = str(name)[:255] if name else None
    user.role = UserRole.ADMIN if email in settings.admin_emails else UserRole.USER
    user.last_login_at = datetime.now(UTC)
    db.flush()
    return user, created


def accept_privacy(db: Session, user: User) -> None:
    user.privacy_accepted_at = datetime.now(UTC)
    user.privacy_version = PRIVACY_POLICY_VERSION
    audit.record(db, "PRIVACY_ACCEPTED", user_id=user.id, version=PRIVACY_POLICY_VERSION)


def has_current_consent(user: User) -> bool:
    """A consent counts only for the policy version it was given to; a missing version counts as none."""
    return user.privacy_accepted_at is not None and user.privacy_version == PRIVACY_POLICY_VERSION


def pseudonymous_id(user_id: object) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def delete_account(db: Session, storage: ObjectStorage, user: User) -> dict[str, int]:
    """Erase originals, structured data, user-scoped products/aliases, sessions and the profile.
    The audit trail keeps only a pseudonymous reference."""
    objects = storage.delete_prefix(user_prefix(user.id))
    receipts = len(db.scalars(delete(Receipt).where(Receipt.user_id == user.id).returning(Receipt.id)).all())
    db.execute(delete(ProductAlias).where(ProductAlias.user_id == user.id))
    db.execute(delete(Product).where(Product.owner_user_id == user.id))
    revoke_all_sessions(db, user.id)
    # Earlier audit rows keep their history but lose the link to the person.
    db.execute(update(AuditEvent).where(AuditEvent.user_id == user.id).values(user_id=None))
    audit.record(
        db,
        "ACCOUNT_DELETED",
        user_id=None,
        actor=ActorType.USER,
        subject=pseudonymous_id(user.id),
        receipts=receipts,
        objects=objects,
    )
    db.delete(user)
    db.flush()
    return {"receipts": receipts, "objects": objects}
