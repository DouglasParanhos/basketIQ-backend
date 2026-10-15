"""OpenID Connect authorization-code flow with PKCE. Google in staging/production; a mock OIDC
server locally (same code path, no bypass)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from basketiq.config import GOOGLE_ISSUER, Settings


class OidcError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OidcClient:
    METADATA_TTL = 3600

    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self.settings = settings
        self._http = http or httpx.Client(timeout=10)
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._jwks: jwt.PyJWKSet | None = None
        self._lock = threading.Lock()

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            if self._metadata is None or time.monotonic() - self._metadata_at > self.METADATA_TTL:
                url = self.settings.oidc_discovery_url or (
                    self.settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
                )
                try:
                    resp = self._http.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise OidcError("provider_unavailable") from exc
                self._metadata = resp.json()
                self._metadata_at = time.monotonic()
                self._jwks = None
            return self._metadata

    def _signing_key(self, kid: str | None) -> jwt.PyJWK:
        """JWKS fetched with our HTTP client; refetched once when the key id is unknown (rotation)."""
        for attempt in range(2):
            if self._jwks is None or attempt == 1:
                try:
                    resp = self._http.get(self._internal(self.metadata()["jwks_uri"]))
                    resp.raise_for_status()
                    self._jwks = jwt.PyJWKSet.from_dict(resp.json())
                except (httpx.HTTPError, jwt.PyJWKSetError) as exc:
                    raise OidcError("provider_unavailable") from exc
            for key in self._jwks.keys:
                if kid is None or key.key_id == kid:
                    return key
        raise OidcError("invalid_id_token")

    def _internal(self, url: str) -> str:
        s = self.settings
        if s.oidc_browser_base_url and s.oidc_internal_base_url and url.startswith(s.oidc_browser_base_url):
            return s.oidc_internal_base_url + url[len(s.oidc_browser_base_url) :]
        return url

    def _browser(self, url: str) -> str:
        s = self.settings
        if s.oidc_browser_base_url and s.oidc_internal_base_url and url.startswith(s.oidc_internal_base_url):
            return s.oidc_browser_base_url + url[len(s.oidc_internal_base_url) :]
        return url

    def authorization_url(self, state: str, nonce: str, code_challenge: str) -> str:
        endpoint = self._browser(self.metadata()["authorization_endpoint"])
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.settings.oidc_redirect_uri,
            # Data minimisation: identity and e-mail only.
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        endpoint = self._internal(self.metadata()["token_endpoint"])
        try:
            resp = self._http.post(
                endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.oidc_redirect_uri,
                    "client_id": self.settings.oidc_client_id,
                    "client_secret": self.settings.oidc_client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OidcError("provider_unavailable") from exc
        if resp.status_code != 200:
            raise OidcError("code_exchange_failed")
        return dict(resp.json())

    def verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        issuer = self.settings.oidc_issuer
        # Google may emit the issuer with or without scheme.
        issuers = [GOOGLE_ISSUER, "accounts.google.com"] if issuer == GOOGLE_ISSUER else [issuer]
        try:
            key = self._signing_key(jwt.get_unverified_header(id_token).get("kid"))
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=self.settings.oidc_client_id,
                issuer=issuers,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise OidcError("invalid_id_token") from exc
        if claims.get("nonce") != nonce:
            raise OidcError("invalid_nonce")
        if issuer == GOOGLE_ISSUER and not claims.get("email_verified"):
            raise OidcError("email_not_verified")
        if not claims.get("email"):
            if not (self.settings.is_local and issuer != GOOGLE_ISSUER):
                raise OidcError("email_missing")
            # Local mock OIDC: the login form only asks for a subject, so derive a stable e-mail.
            claims["email"] = f"{claims['sub']}@basketiq.local"
            claims.setdefault("name", str(claims["sub"]))
        return dict(claims)
