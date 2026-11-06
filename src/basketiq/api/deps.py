from __future__ import annotations

import hmac
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

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
PASSKEY_COOKIE = "biq_webauthn"  # id of the pending one-time passkey challenge
DEVICE_PASSKEYS_COOKIE = "biq_passkeys"  # the passkeys this phone holds (see `set_device_passkeys`)
DEVICE_PASSKEYS_MAX = 5
DEVICE_PASSKEYS_SECONDS = 400 * 86400  # the longest a browser keeps a cookie
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


# scope="function": the teardown (commit) runs before the response is sent, not after it (FastAPI's
# default), so a client acting on the response at once (upload-init → process) finds the row.
Db = Annotated[Session, Depends(get_db, scope="function")]
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


def set_passkey_challenge_cookie(
    response: Response, settings: Settings, challenge_id: str, max_age: int
) -> None:
    # Path /v1 covers registration (/v1/me) and sign-in (/v1/auth). SameSite=Lax: not sent on cross-site POSTs
    response.set_cookie(
        PASSKEY_COOKIE,
        challenge_id,
        httponly=True,
        path="/v1",
        max_age=max_age,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_passkey_challenge_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(PASSKEY_COOKIE, path="/v1", secure=settings.cookie_secure, samesite="lax")


def device_passkeys(request: Request) -> list[bytes]:
    """The credential ids of the passkeys made on this phone (see `set_device_passkeys`)."""
    ids: list[bytes] = []
    for part in request.cookies.get(DEVICE_PASSKEYS_COOKIE, "").split(".")[:DEVICE_PASSKEYS_MAX]:
        try:
            if credential_id := base64url_to_bytes(part):
                ids.append(credential_id)
        except ValueError:
            continue
    return ids


def set_device_passkeys(response: Response, settings: Settings, ids: list[bytes]) -> None:
    """Remember on this phone which passkeys it holds, so the sign-in asks only for them: an HttpOnly cookie
    with their credential ids (random, not personal data), the newest first."""
    kept = list(dict.fromkeys(ids))[:DEVICE_PASSKEYS_MAX]
    if not kept:
        response.delete_cookie(
            DEVICE_PASSKEYS_COOKIE, path="/v1", secure=settings.cookie_secure, samesite="lax"
        )
        return
    response.set_cookie(
        DEVICE_PASSKEYS_COOKIE,
        ".".join(bytes_to_base64url(credential_id) for credential_id in kept),
        httponly=True,
        path="/v1",
        max_age=DEVICE_PASSKEYS_SECONDS,
        secure=settings.cookie_secure,
        samesite="lax",
    )


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
