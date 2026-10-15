from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import func, select

from basketiq.api.routers.auth import get_oidc
from basketiq.auth.oidc import OidcClient
from basketiq.config import get_settings
from basketiq.db.models import User, UserSession

ISSUER = "https://idp.test"


class FakeIdP:
    """Minimal OIDC provider: discovery, JWKS and token endpoint (httpx MockTransport)."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [jwk]}
        self.next_claims: dict[str, Any] = {}
        self.nonce: str | None = None
        self.token_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json=self.jwks)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["code_verifier"], "PKCE verifier must be sent"
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "aud": "basketiq-test",
                "sub": "subject-1",
                "email": "Maria@Example.test",
                "email_verified": True,
                "name": "Maria",
                "iat": now,
                "exp": now + 300,
                "nonce": self.nonce,
                **self.next_claims,
            }
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(self.token_status, json={"id_token": token, "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def idp(client: TestClient) -> FakeIdP:
    from basketiq.api.main import app

    fake = FakeIdP()
    oidc = OidcClient(get_settings(), http=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    app.dependency_overrides[get_oidc] = lambda: oidc
    return fake


def sign_in(client: TestClient, idp: FakeIdP) -> httpx.Response:
    start = client.get("/v1/auth/login", follow_redirects=False)
    assert start.status_code == 302
    params = parse_qs(urlparse(start.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"] and params["scope"] == ["openid email profile"]
    idp.nonce = params["nonce"][0]
    return client.get(f"/v1/auth/callback?code=abc&state={params['state'][0]}", follow_redirects=False)


@pytest.mark.req("F-01")
def test_first_login_creates_profile_and_session(client: TestClient, idp: FakeIdP, db: Any) -> None:
    resp = sign_in(client, idp)
    assert resp.status_code == 302 and resp.headers["location"] == "http://frontend.test/"
    assert {"biq_at", "biq_rt", "biq_csrf"} <= set(client.cookies.keys())
    me = client.get("/v1/me").json()
    assert me["email"] == "maria@example.test"
    assert me["privacy_accepted_at"] is None
    # Data minimisation: only the needed attributes are stored.
    user = db.scalar(select(User))
    assert user.google_subject == f"{ISSUER}|subject-1"


@pytest.mark.req("F-02")
def test_second_login_recovers_profile_without_duplicate(client: TestClient, idp: FakeIdP, db: Any) -> None:
    sign_in(client, idp)
    client.cookies.clear()
    sign_in(client, idp)
    assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.req("F-03")
def test_cancelled_login_returns_clear_error(client: TestClient, idp: FakeIdP) -> None:
    client.get("/v1/auth/login", follow_redirects=False)
    resp = client.get("/v1/auth/callback?error=access_denied&state=x", follow_redirects=False)
    assert resp.headers["location"] == "http://frontend.test/login?error=cancelled"
    assert "biq_at" not in client.cookies


def test_state_mismatch_is_rejected(client: TestClient, idp: FakeIdP) -> None:
    client.get("/v1/auth/login", follow_redirects=False)
    resp = client.get("/v1/auth/callback?code=abc&state=forged", follow_redirects=False)
    assert resp.headers["location"].endswith("error=invalid_request")


def test_wrong_audience_or_nonce_is_rejected(client: TestClient, idp: FakeIdP) -> None:
    idp.next_claims = {"aud": "someone-else"}
    assert sign_in(client, idp).headers["location"].endswith("error=login_failed")
    client.cookies.clear()
    idp.next_claims = {"nonce": "replayed"}
    assert sign_in(client, idp).headers["location"].endswith("error=login_failed")


@pytest.mark.req("F-03a")
def test_no_password_or_other_login_method_exists(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    auth_paths = {p for p in paths if p.startswith("/v1/auth")}
    assert auth_paths == {"/v1/auth/login", "/v1/auth/callback", "/v1/auth/refresh", "/v1/auth/logout"}
    assert client.post("/v1/auth/password", json={"email": "a", "password": "b"}).status_code in (
        403,
        404,
        405,
    )


def test_production_rejects_non_google_issuer() -> None:
    from basketiq.config import Settings

    with pytest.raises(ValueError, match="Only Google"):
        Settings(env="production", oidc_issuer="https://idp.test", jwt_secret="x" * 40, cookie_secure=True)


@pytest.mark.req("SEC-tokens")
def test_refresh_rotates_and_detects_reuse(client: TestClient, idp: FakeIdP, db: Any) -> None:
    sign_in(client, idp)
    client.headers["X-CSRF-Token"] = client.cookies["biq_csrf"]
    old_refresh = client.cookies.get("biq_rt", path="/v1/auth")
    assert client.post("/v1/auth/refresh").status_code == 204
    assert client.cookies.get("biq_rt", path="/v1/auth") != old_refresh
    # Replaying the rotated-out token revokes the session.
    attacker = TestClient(client.app)
    attacker.cookies.set("biq_rt", old_refresh, path="/v1/auth")
    attacker.cookies.set("biq_csrf", "c")
    assert attacker.post("/v1/auth/refresh", headers={"X-CSRF-Token": "c"}).status_code == 401
    db.expire_all()
    assert db.scalar(select(UserSession)).revoked_at is not None
    assert client.get("/v1/me").status_code == 401


@pytest.mark.req("SEC-tokens")
def test_logout_revokes_immediately(client: TestClient, idp: FakeIdP) -> None:
    sign_in(client, idp)
    access = client.cookies["biq_at"]
    client.headers["X-CSRF-Token"] = client.cookies["biq_csrf"]
    assert client.post("/v1/auth/logout").status_code == 204
    client.cookies.set("biq_at", access)  # a copied, still unexpired access token
    assert client.get("/v1/me").status_code == 401


@pytest.mark.req("SEC-tokens")
def test_expired_and_tampered_tokens(client: TestClient, login: Any) -> None:
    login(client)
    good = client.cookies["biq_at"]
    client.cookies.set("biq_at", good[:-2] + ("AA" if not good.endswith("AA") else "BB"))
    assert client.get("/v1/me").json()["code"] == "invalid_token"
    claims = jwt.decode(good, options={"verify_signature": False})
    claims["exp"] = int(time.time()) - 10
    client.cookies.set("biq_at", jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256"))
    assert client.get("/v1/me").json()["code"] == "token_expired"


@pytest.mark.req("SEC-csrf")
def test_unsafe_requests_need_csrf_header(client: TestClient, login: Any) -> None:
    login(client)
    del client.headers["X-CSRF-Token"]
    resp = client.post("/v1/me/privacy-consent")
    assert resp.status_code == 403 and resp.json()["code"] == "csrf_failed"


@pytest.mark.req("SEC-ratelimit")
def test_login_is_rate_limited(client: TestClient, idp: FakeIdP) -> None:
    statuses = [client.get("/v1/auth/login", follow_redirects=False).status_code for _ in range(12)]
    assert 429 in statuses
    body = client.get("/v1/auth/login", follow_redirects=False).json()
    assert body["code"] == "rate_limited" and body["request_id"]


def test_admin_role_from_configuration(client: TestClient, idp: FakeIdP) -> None:
    idp.next_claims = {"email": "admin@example.test", "sub": "admin-1"}
    sign_in(client, idp)
    assert client.get("/v1/me").json()["role"] == "admin"
