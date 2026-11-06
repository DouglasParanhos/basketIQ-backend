"""Passkeys (RF-01, spec 016): register after a Google sign-in, then sign in with the phone's lock."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.cose import COSEKTY, COSEAlgorithmIdentifier, COSEKey
from webauthn.helpers.encode_cbor import encode_cbor

from basketiq.config import GOOGLE_ISSUER, Settings
from basketiq.db.models import AuditEvent, User, WebAuthnChallenge, WebAuthnCredential

ORIGIN = "http://frontend.test"  # BASKETIQ_FRONTEND_BASE_URL in tests/conftest.py
RP_ID = "frontend.test"
UP, UV, AT = 0x01, 0x04, 0x40
PRODUCTION: dict[str, Any] = {
    "env": "production",
    "oidc_issuer": GOOGLE_ISSUER,
    "jwt_secret": "x" * 40,
    "cookie_secure": True,
}


class SoftAuthenticator:
    """A software passkey: P-256 key, attestation `none`, discoverable (returns the user handle)."""

    def __init__(
        self, *, origin: str = ORIGIN, rp_id: str = RP_ID, verify_user: bool = True, counter: bool = False
    ):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.origin, self.rp_id, self.verify_user = origin, rp_id, verify_user
        self.counter = counter  # False: a synced passkey, which always reports 0
        self.sign_count = 0
        self.user_handle = b""

    def _client_data(self, kind: str, challenge: str) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def _auth_data(self, extra_flags: int = 0) -> bytes:
        flags = UP | (UV if self.verify_user else 0) | extra_flags
        rp_id_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_id_hash + bytes([flags]) + self.sign_count.to_bytes(4, "big")

    def _credential(self, response: dict[str, Any]) -> dict[str, Any]:
        credential_id = bytes_to_base64url(self.credential_id)
        return {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        numbers = self.key.public_key().public_numbers()
        cose = {
            COSEKey.KTY: COSEKTY.EC2,
            COSEKey.ALG: COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEKey.CRV: 1,
            COSEKey.X: numbers.x.to_bytes(32, "big"),
            COSEKey.Y: numbers.y.to_bytes(32, "big"),
        }
        auth_data = (
            self._auth_data(AT)
            + bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + encode_cbor({int(k): v for k, v in cose.items()})
        )
        attestation = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"])
        return self._credential(
            {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal", "hybrid"],
            }
        )

    def get(self, options: dict[str, Any]) -> dict[str, Any]:
        if self.counter:
            self.sign_count += 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = self._auth_data()
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return self._credential(
            {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(self.user_handle),
            }
        )


def register(client: TestClient, phone: SoftAuthenticator) -> Any:
    options = client.post("/v1/me/passkeys/options")
    assert options.status_code == 200, options.text
    return client.post("/v1/me/passkeys", json=phone.create(options.json()))


def passkey_sign_in(client: TestClient, phone: SoftAuthenticator) -> Any:
    options = client.post("/v1/auth/passkey/options")
    assert options.status_code == 200, options.text
    return client.post("/v1/auth/passkey/verify", json=phone.get(options.json()))


def sign_out(client: TestClient) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


@pytest.fixture
def registered(client: TestClient, login: Any) -> tuple[User, SoftAuthenticator]:
    user = login(client)
    phone = SoftAuthenticator()
    assert register(client, phone).status_code == 201
    sign_out(client)
    return user, phone


@pytest.mark.req("F-206")
def test_register_a_passkey_after_signing_in(client: TestClient, login: Any, db: Session) -> None:
    user = login(client)
    options = client.post("/v1/me/passkeys/options").json()
    assert options["rp"] == {"id": RP_ID, "name": "Super Mercado"}
    assert base64url_to_bytes(options["user"]["id"]) == user.id.bytes  # random id, not the e-mail
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["attestation"] == "none"
    resp = client.post("/v1/me/passkeys", json=SoftAuthenticator().create(options))
    assert resp.status_code == 201
    assert set(resp.json()) == {"id", "created_at", "last_used_at"} and resp.json()["last_used_at"] is None
    assert [p["id"] for p in client.get("/v1/me/passkeys").json()] == [resp.json()["id"]]
    stored = db.scalar(select(WebAuthnCredential))
    assert stored.transports == ["internal", "hybrid"] and stored.sign_count == 0
    assert db.scalar(select(AuditEvent.event_type).where(AuditEvent.event_type == "PASSKEY_ADDED"))


@pytest.mark.req("F-206")
def test_registering_needs_a_session_csrf_and_the_current_consent(client: TestClient, login: Any) -> None:
    assert client.post("/v1/me/passkeys/options").status_code in (401, 403)
    login(client, consent=False)
    resp = client.post("/v1/me/passkeys/options")
    assert resp.status_code == 403 and resp.json()["code"] == "privacy_consent_required"
    sign_out(client)
    login(client)
    client.headers.pop("X-CSRF-Token")
    assert client.post("/v1/me/passkeys/options").json()["code"] == "csrf_failed"


@pytest.mark.req("F-206")
def test_the_same_passkey_cannot_be_registered_twice(client: TestClient, login: Any) -> None:
    login(client)
    phone = SoftAuthenticator()
    assert register(client, phone).status_code == 201
    options = client.post("/v1/me/passkeys/options").json()
    assert [c["id"] for c in options["excludeCredentials"]] == [bytes_to_base64url(phone.credential_id)]
    resp = client.post("/v1/me/passkeys", json=phone.create(options))
    assert resp.status_code == 409 and resp.json()["code"] == "passkey_exists"


@pytest.mark.req("F-206")
def test_a_registration_without_user_verification_is_rejected(client: TestClient, login: Any) -> None:
    login(client)
    resp = register(client, SoftAuthenticator(verify_user=False))
    assert resp.status_code == 400 and resp.json()["code"] == "passkey_invalid"


@pytest.mark.req("F-207")
def test_sign_in_with_a_passkey(
    client: TestClient, registered: tuple[User, SoftAuthenticator], db: Session
) -> None:
    user, phone = registered
    resp = passkey_sign_in(client, phone)
    assert resp.status_code == 204
    assert {"biq_at", "biq_csrf"} <= set(client.cookies.keys())
    assert client.cookies.get("biq_rt", path="/v1/auth")
    assert client.get("/v1/me").json()["id"] == str(user.id)
    db.expire_all()
    assert db.scalar(select(WebAuthnCredential.last_used_at)) is not None
    assert db.get(User, user.id).last_login_at is not None
    login_event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "LOGIN"))
    assert login_event.payload_redacted["method"] == "passkey"


@pytest.mark.req("F-207")
def test_passkey_sign_in_recomputes_the_role(client: TestClient, login: Any, db: Session) -> None:
    user = login(client, "admin@example.test", role="user")
    phone = SoftAuthenticator()
    register(client, phone)
    sign_out(client)
    assert passkey_sign_in(client, phone).status_code == 204
    db.expire_all()
    assert db.get(User, user.id).role == "admin"


@pytest.mark.req("F-208")
def test_a_challenge_is_used_only_once(
    client: TestClient, registered: tuple[User, SoftAuthenticator]
) -> None:
    _, phone = registered
    options = client.post("/v1/auth/passkey/options")
    challenge_cookie = client.cookies["biq_webauthn"]
    assertion = phone.get(options.json())
    assert client.post("/v1/auth/passkey/verify", json=assertion).status_code == 204
    sign_out(client)
    client.cookies.set("biq_webauthn", challenge_cookie, path="/v1")
    replay = client.post("/v1/auth/passkey/verify", json=assertion)
    assert replay.status_code == 401 and replay.json()["code"] == "passkey_invalid"


@pytest.mark.req("F-208")
def test_an_expired_missing_or_registration_challenge_is_rejected(
    client: TestClient, registered: tuple[User, SoftAuthenticator], login: Any, db: Session
) -> None:
    _, phone = registered
    options = client.post("/v1/auth/passkey/options").json()
    db.execute(update(WebAuthnChallenge).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    assert client.post("/v1/auth/passkey/verify", json=phone.get(options)).status_code == 401

    client.cookies.clear()
    assert client.post("/v1/auth/passkey/verify", json=phone.get(options)).status_code == 401

    # A registration challenge of someone signed in does not open a sign-in.
    login(client)
    register_options = client.post("/v1/me/passkeys/options").json()
    sign_out(client)
    client.cookies.set("biq_webauthn", db.scalar(select(WebAuthnChallenge.id)), path="/v1")
    assert client.post("/v1/auth/passkey/verify", json=phone.get(register_options)).status_code == 401


@pytest.mark.req("F-209")
@pytest.mark.parametrize(
    "tamper",
    [
        {"origin": "https://evil.test"},
        {"rp_id": "evil.test"},
        {"verify_user": False},
    ],
    ids=["other-origin", "other-rp-id", "no-user-verification"],
)
def test_an_assertion_from_another_origin_rp_or_without_verification_is_rejected(
    client: TestClient, registered: tuple[User, SoftAuthenticator], tamper: dict[str, Any]
) -> None:
    _, phone = registered
    for name, value in tamper.items():
        setattr(phone, name, value)
    resp = passkey_sign_in(client, phone)
    assert resp.status_code == 401 and resp.json()["code"] == "passkey_invalid"
    assert "biq_at" not in client.cookies


@pytest.mark.req("F-209")
def test_a_forged_signature_or_another_users_handle_is_rejected(
    client: TestClient, registered: tuple[User, SoftAuthenticator]
) -> None:
    _, phone = registered
    key, handle = phone.key, phone.user_handle
    phone.key = ec.generate_private_key(ec.SECP256R1())  # same credential id, wrong key
    assert passkey_sign_in(client, phone).status_code == 401
    phone.key, phone.user_handle = key, secrets.token_bytes(16)
    assert passkey_sign_in(client, phone).status_code == 401
    phone.user_handle = handle
    assert passkey_sign_in(client, phone).status_code == 204


@pytest.mark.req("F-210")
def test_unknown_or_removed_passkey_or_inactive_user_cannot_sign_in(
    client: TestClient, login: Any, db: Session
) -> None:
    assert passkey_sign_in(client, SoftAuthenticator()).status_code == 401

    user = login(client)
    phone = SoftAuthenticator()
    passkey_id = register(client, phone).json()["id"]
    assert client.delete(f"/v1/me/passkeys/{passkey_id}").status_code == 204
    sign_out(client)
    assert passkey_sign_in(client, phone).status_code == 401

    login(client)
    other = SoftAuthenticator()
    register(client, other)
    sign_out(client)
    db.execute(update(User).where(User.id != user.id).values(status="disabled"))
    db.commit()
    assert passkey_sign_in(client, other).status_code == 401


@pytest.mark.req("F-211")
def test_only_ones_own_passkeys_are_listed_and_removed(client: TestClient, login: Any, db: Session) -> None:
    login(client)
    mine = register(client, SoftAuthenticator()).json()["id"]
    sign_out(client)
    login(client)
    assert client.get("/v1/me/passkeys").json() == []
    resp = client.delete(f"/v1/me/passkeys/{mine}")
    assert resp.status_code == 404 and resp.json()["code"] == "not_found"
    assert db.scalar(select(WebAuthnCredential.id)) is not None
    assert not db.scalar(select(AuditEvent).where(AuditEvent.event_type == "PASSKEY_REMOVED"))


@pytest.mark.req("F-212")
def test_deleting_the_account_removes_its_passkeys(client: TestClient, login: Any, db: Session) -> None:
    login(client)
    register(client, SoftAuthenticator())
    client.post("/v1/me/passkeys/options")  # a pending challenge goes too
    assert client.delete("/v1/me").status_code == 200
    assert db.scalar(select(WebAuthnCredential)) is None
    assert db.scalar(select(WebAuthnChallenge)) is None


@pytest.mark.req("F-213")
def test_sign_count_zero_is_accepted_and_a_regression_is_rejected(client: TestClient, login: Any) -> None:
    login(client)
    synced, counting = SoftAuthenticator(), SoftAuthenticator(counter=True)
    register(client, synced)
    register(client, counting)
    sign_out(client)
    for _ in range(2):  # iCloud Keychain and Google Password Manager always send 0
        assert passkey_sign_in(client, synced).status_code == 204
        sign_out(client)
    assert passkey_sign_in(client, counting).status_code == 204
    sign_out(client)
    counting.sign_count = 0  # a cloned authenticator would go backwards
    assert passkey_sign_in(client, counting).status_code == 401


@pytest.mark.req("F-214")
def test_passkey_sign_in_is_rate_limited(client: TestClient) -> None:
    statuses = [client.post("/v1/auth/passkey/options").status_code for _ in range(12)]
    assert 429 in statuses
    assert client.post("/v1/auth/passkey/options").json()["code"] == "rate_limited"


@pytest.mark.req("F-215")
def test_dismissing_the_passkey_offer_is_kept_once(client: TestClient, login: Any) -> None:
    login(client)
    assert client.get("/v1/me/profile").json()["passkey_offer_dismissed_at"] is None
    first = client.patch("/v1/me/profile", json={"passkey_offer_dismissed": True}).json()
    assert first["passkey_offer_dismissed_at"] is not None
    again = client.patch("/v1/me/profile", json={"passkey_offer_dismissed": True}).json()
    assert again["passkey_offer_dismissed_at"] == first["passkey_offer_dismissed_at"]
    assert again["onboarding_completed_at"] is None


@pytest.mark.req("RF-01")
@pytest.mark.parametrize(
    ("frontend", "rp_id"),
    [
        ("https://192.168.1.80", None),
        ("https://app.example.pt", "localhost"),
        ("https://app.example.pt", "other.pt"),
    ],
)
def test_production_requires_a_real_rp_domain(frontend: str, rp_id: str | None) -> None:
    with pytest.raises(ValueError, match="WEBAUTHN_RP_ID"):
        Settings(**PRODUCTION, frontend_base_url=frontend, webauthn_rp_id=rp_id)


@pytest.mark.req("RF-01")
def test_production_accepts_the_frontend_host_or_a_parent_domain() -> None:
    assert (
        Settings(**PRODUCTION, frontend_base_url="https://app.example.pt").passkey_rp_id == "app.example.pt"
    )
    parent = Settings(**PRODUCTION, frontend_base_url="https://app.example.pt", webauthn_rp_id="example.pt")
    assert parent.passkey_rp_id == "example.pt"
