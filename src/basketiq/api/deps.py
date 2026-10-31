from __future__ import annotations

import hmac
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from basketiq.api.errors import ApiError
from basketiq.auth.sessions import IssuedTokens, SessionError, decode_access_token
from basketiq.config import Settings, get_settings
from basketiq.db.models import User, UserSession
from basketiq.db.session import get_sessionmaker
from basketiq.domain.status import UserRole
from basketiq.services import accounts

ACCESS_COOKIE = "biq_at"
REFRESH_COOKIE = "biq_rt"
CSRF_COOKIE = "biq_csrf"
STATE_COOKIE = "biq_oauth"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_db() -> Iterator[Session]:
    """One transaction per request: committed on success, rolled back on any error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


Db = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_today(settings: SettingsDep) -> date:
    """Today's date in the configured timezone (a dependency so tests can fix the day)."""
    return datetime.now(ZoneInfo(settings.timezone)).date()


Today = Annotated[date, Depends(get_today)]


def set_session_cookies(response: Response, settings: Settings, tokens: IssuedTokens) -> None:
    secure = settings.cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        httponly=True,
        path="/",
        max_age=settings.access_token_ttl_seconds,
        secure=secure,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        httponly=True,
        path="/v1/auth",
        max_age=settings.refresh_token_ttl_days * 86400,
        secure=secure,
        samesite="lax",
    )
    # Readable by the SPA, which echoes it in the X-CSRF-Token header (double submit).
    response.set_cookie(
        CSRF_COOKIE,
        tokens.csrf_token,
        httponly=False,
        path="/",
        max_age=settings.refresh_token_ttl_days * 86400,
        secure=secure,
        samesite="lax",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/v1/auth"), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, secure=settings.cookie_secure, samesite="lax")


def csrf_protect(request: Request) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise ApiError(403, "csrf_failed", "Pedido rejeitado (CSRF).")


def current_user(request: Request, db: Db, settings: SettingsDep) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise ApiError(401, "unauthenticated", "Sessão necessária.")
    try:
        claims = decode_access_token(settings, token)
    except SessionError as exc:
        raise ApiError(401, exc.code, "Sessão expirada ou inválida.") from exc
    session = db.get(UserSession, uuid.UUID(claims["sid"]))
    # Checked on every request so that logout/revocation takes effect immediately.
    if session is None or session.revoked_at is not None or session.expires_at <= datetime.now(UTC):
        raise ApiError(401, "session_revoked", "Sessão terminada.")
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or user.status != "active":
        raise ApiError(401, "unauthenticated", "Sessão necessária.")
    request.state.session_id = session.id
    request.state.user_id = user.id
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def consented_user(user: CurrentUser) -> User:
    # `current_user` stays open: without a valid consent one can still consent, see the account or leave.
    if not accounts.has_current_consent(user):
        raise ApiError(403, "privacy_consent_required", "Aceite a política de privacidade para continuar.")
    return user


ConsentedUser = Annotated[User, Depends(consented_user)]


def admin_user(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise ApiError(403, "forbidden", "Acesso reservado a operadores.")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
