"""Short-lived access tokens + rotating, revocable refresh tokens (spec técnica §8)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from basketiq.config import Settings
from basketiq.db.models import User, UserSession

ALGORITHM = "HS256"
CLOCK_LEEWAY_SECONDS = 30
log = logging.getLogger(__name__)


class SessionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    csrf_token: str
    session_id: uuid.UUID


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(settings: Settings, user: User, session_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "sid": str(session_id),
        "role": user.role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        # Leeway absorbs small clock steps between API instances (also seen on WSL2 hosts).
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "sid"]},
            leeway=CLOCK_LEEWAY_SECONDS,
        )
    except jwt.ExpiredSignatureError as exc:
        raise SessionError("token_expired") from exc
    except jwt.PyJWTError as exc:
        log.warning("access_token_rejected", extra={"error_type": type(exc).__name__})
        raise SessionError("invalid_token") from exc
    if claims.get("typ") != "access":
        raise SessionError("invalid_token")
    return claims


def create_session(db: Session, settings: Settings, user: User) -> IssuedTokens:
    refresh = secrets.token_urlsafe(48)
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=_hash(refresh),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
    )
    db.add(session)
    db.flush()
    return IssuedTokens(
        access_token=issue_access_token(settings, user, session.id),
        refresh_token=refresh,
        csrf_token=secrets.token_urlsafe(32),
        session_id=session.id,
    )


def rotate_session(db: Session, settings: Settings, refresh_token: str) -> tuple[User, IssuedTokens]:
    token_hash = _hash(refresh_token)
    now = datetime.now(UTC)
    session = db.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == token_hash).with_for_update()
    )
    if session is None:
        # A rotated-out token presented again means it leaked: revoke the whole session.
        reused = db.scalar(select(UserSession).where(UserSession.previous_refresh_token_hash == token_hash))
        if reused is not None and reused.revoked_at is None:
            reused.revoked_at = now
            db.commit()  # must survive the error response's rollback
        raise SessionError("invalid_refresh_token")
    if session.revoked_at is not None or session.expires_at <= now:
        raise SessionError("session_expired")
    user = db.get(User, session.user_id)
    if user is None or user.status != "active":
        raise SessionError("user_inactive")
    new_refresh = secrets.token_urlsafe(48)
    session.previous_refresh_token_hash = token_hash
    session.refresh_token_hash = _hash(new_refresh)
    session.rotated_at = now
    db.flush()
    return user, IssuedTokens(
        access_token=issue_access_token(settings, user, session.id),
        refresh_token=new_refresh,
        csrf_token=secrets.token_urlsafe(32),
        session_id=session.id,
    )


def find_session_by_refresh(db: Session, refresh_token: str) -> UserSession | None:
    return db.scalar(select(UserSession).where(UserSession.refresh_token_hash == _hash(refresh_token)))


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def sign_state(settings: Settings, data: dict[str, str], ttl_seconds: int = 600) -> str:
    payload = {**data, "typ": "oauth_state", "exp": datetime.now(UTC) + timedelta(seconds=ttl_seconds)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def read_state(settings: Settings, token: str) -> dict[str, Any]:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise SessionError("invalid_state") from exc
    if data.get("typ") != "oauth_state":
        raise SessionError("invalid_state")
    return data
