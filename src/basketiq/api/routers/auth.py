from __future__ import annotations

import contextlib
import logging
import secrets
import uuid
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from basketiq.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    STATE_COOKIE,
    Db,
    SettingsDep,
    clear_session_cookies,
    csrf_protect,
    set_session_cookies,
)
from basketiq.api.errors import ApiError, ErrorBody, error_response
from basketiq.api.ratelimit import limiter
from basketiq.auth.oidc import OidcClient, OidcError, pkce_pair
from basketiq.auth.sessions import (
    SessionError,
    create_session,
    decode_access_token,
    find_session_by_refresh,
    read_state,
    revoke_session,
    rotate_session,
    sign_state,
)
from basketiq.config import get_settings
from basketiq.db.models import UserSession
from basketiq.services import accounts, audit

router = APIRouter(prefix="/v1/auth", tags=["auth"])
log = logging.getLogger(__name__)


@lru_cache
def _oidc_client() -> OidcClient:
    return OidcClient(get_settings())


def get_oidc() -> OidcClient:
    return _oidc_client()


Oidc = Annotated[OidcClient, Depends(get_oidc)]


def _frontend(settings_base: str, path: str, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings_base}{path}{query}"


@router.get("/login", summary="Start Google sign-in (redirects to the identity provider)")
@limiter.limit(lambda: get_settings().rate_limit_login)
def login(request: Request, settings: SettingsDep, oidc: Oidc) -> RedirectResponse:
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    try:
        url = oidc.authorization_url(state, nonce, challenge)
    except OidcError:
        return RedirectResponse(
            _frontend(settings.frontend_base_url, "/login", error="provider_unavailable"), 302
        )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        sign_state(settings, {"state": state, "nonce": nonce, "verifier": verifier}),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/v1/auth",
        max_age=600,
    )
    return response


@router.get("/callback", summary="OIDC redirect target")
def callback(
    request: Request,
    db: Db,
    settings: SettingsDep,
    oidc: Oidc,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    def fail(reason: str) -> RedirectResponse:
        resp = RedirectResponse(_frontend(settings.frontend_base_url, "/login", error=reason), 302)
        resp.delete_cookie(STATE_COOKIE, path="/v1/auth")
        return resp

    if error:
        # F-03: user cancelled at the provider (access_denied) or provider error.
        return fail("cancelled" if error == "access_denied" else "provider_error")
    cookie = request.cookies.get(STATE_COOKIE)
    if not code or not state or not cookie:
        return fail("invalid_request")
    try:
        saved = read_state(settings, cookie)
    except SessionError:
        return fail("invalid_request")
    if not secrets.compare_digest(saved["state"], state):
        return fail("invalid_request")
    try:
        tokens = oidc.exchange_code(code, saved["verifier"])
        claims = oidc.verify_id_token(tokens.get("id_token", ""), saved["nonce"])
    except OidcError as exc:
        log.warning("oidc_failed", extra={"reason": exc.code})
        return fail("login_failed")

    user, created = accounts.upsert_from_claims(db, settings, claims)
    issued = create_session(db, settings, user)
    audit.record(db, "LOGIN", user_id=user.id, first_login=created)
    response = RedirectResponse(_frontend(settings.frontend_base_url, "/"), 302)
    response.delete_cookie(STATE_COOKIE, path="/v1/auth")
    set_session_cookies(response, settings, issued)
    return response


@router.post(
    "/refresh",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(csrf_protect)],
    responses={401: {"model": ErrorBody}},
    summary="Rotate the session tokens",
)
def refresh(request: Request, db: Db, settings: SettingsDep) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise ApiError(401, "unauthenticated", "Sessão necessária.")
    try:
        _, issued = rotate_session(db, settings, token)
    except SessionError as exc:
        failed = error_response(401, exc.code, "Sessão expirada. Entre novamente.")
        clear_session_cookies(failed, settings)
        return failed
    response = Response(status_code=204)
    set_session_cookies(response, settings, issued)
    return response


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(csrf_protect)],
    summary="End the current session (works with an expired access token)",
)
def logout(request: Request, db: Db, settings: SettingsDep) -> Response:
    session = None
    access = request.cookies.get(ACCESS_COOKIE)
    if access:
        with contextlib.suppress(SessionError):
            session = db.get(UserSession, uuid.UUID(decode_access_token(settings, access)["sid"]))
    if session is None and (refresh_token := request.cookies.get(REFRESH_COOKIE)):
        session = find_session_by_refresh(db, refresh_token)
    if session is not None:
        revoke_session(db, session.id)
        audit.record(db, "LOGOUT", user_id=session.user_id)
    response = Response(status_code=204)
    clear_session_cookies(response, settings)
    return response
