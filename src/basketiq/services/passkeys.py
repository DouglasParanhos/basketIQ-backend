"""Passkeys (RF-01, spec 016): an alternative way in for an existing account, verified by the phone's lock
(fingerprint, face or PIN). The account is still created and recovered with Google.

Only the public key, the signature counter and dates are stored. Challenges are one-time and kept server side:
a used or expired one is never accepted again.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_authentication_credential_json, parse_registration_credential_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from basketiq.config import Settings
from basketiq.db.models import User, WebAuthnChallenge, WebAuthnCredential
from basketiq.services import audit

CHALLENGE_SECONDS = 300
REGISTER, LOGIN = "register", "login"


class PasskeyError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PasskeyExistsError(PasskeyError):
    def __init__(self) -> None:
        super().__init__("passkey_exists")


def _issue_challenge(db: Session, challenge: bytes, purpose: str, user_id: uuid.UUID | None) -> str:
    now = datetime.now(UTC)
    db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at <= now))
    challenge_id = secrets.token_urlsafe(32)
    db.add(
        WebAuthnChallenge(
            id=challenge_id,
            challenge=challenge,
            purpose=purpose,
            user_id=user_id,
            expires_at=now + timedelta(seconds=CHALLENGE_SECONDS),
        )
    )
    db.flush()
    return challenge_id


def _consume_challenge(
    db: Session, challenge_id: str | None, purpose: str, user_id: uuid.UUID | None
) -> bytes:
    """Delete the challenge and return it. Absent, expired, of another purpose or another person: rejected."""
    if not challenge_id:
        raise PasskeyError("passkey_invalid")
    row = db.scalars(
        delete(WebAuthnChallenge).where(WebAuthnChallenge.id == challenge_id).returning(WebAuthnChallenge)
    ).one_or_none()
    if row is None or row.purpose != purpose or row.user_id != user_id or row.expires_at <= datetime.now(UTC):
        raise PasskeyError("passkey_invalid")
    return row.challenge


def _options_dict(options: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(options_to_json(options))
    return result


def registration_options(
    db: Session, settings: Settings, user: User, rp_name: str
) -> tuple[dict[str, Any], str]:
    """Options for `navigator.credentials.create()` and the id of their challenge."""
    existing = db.scalars(
        select(WebAuthnCredential.credential_id).where(WebAuthnCredential.user_id == user.id)
    )
    options = generate_registration_options(
        rp_id=settings.passkey_rp_id,
        rp_name=rp_name,
        user_id=user.id.bytes,  # random, not personal data
        user_name=user.email,
        user_display_name=user.display_name or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=credential_id) for credential_id in existing],
    )
    return _options_dict(options), _issue_challenge(db, options.challenge, REGISTER, user.id)


def register(
    db: Session, settings: Settings, user: User, credential: dict[str, Any], challenge_id: str | None
) -> WebAuthnCredential:
    challenge = _consume_challenge(db, challenge_id, REGISTER, user.id)
    try:
        parsed = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id,
            expected_origin=settings.frontend_base_url,
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        raise PasskeyError("passkey_invalid") from exc
    if db.scalar(
        select(WebAuthnCredential.id).where(WebAuthnCredential.credential_id == verified.credential_id)
    ):
        raise PasskeyExistsError
    passkey = WebAuthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=[t.value for t in parsed.response.transports or []],
    )
    db.add(passkey)
    db.flush()
    audit.record(db, "PASSKEY_ADDED", user_id=user.id, passkey_id=passkey.id)
    return passkey


def authentication_options(db: Session, settings: Settings) -> tuple[dict[str, Any], str]:
    """Options for `navigator.credentials.get()`: no credential list, the phone offers its passkeys."""
    options = generate_authentication_options(
        rp_id=settings.passkey_rp_id, user_verification=UserVerificationRequirement.REQUIRED
    )
    return _options_dict(options), _issue_challenge(db, options.challenge, LOGIN, None)


def authenticate(
    db: Session, settings: Settings, credential: dict[str, Any], challenge_id: str | None
) -> User:
    challenge = _consume_challenge(db, challenge_id, LOGIN, None)
    try:
        parsed = parse_authentication_credential_json(credential)
        passkey = db.scalars(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.credential_id == parsed.raw_id)
            .with_for_update()
        ).one_or_none()
        # A discoverable credential always returns the user handle it was created with.
        if passkey is None or parsed.response.user_handle != passkey.user_id.bytes:
            raise PasskeyError("passkey_invalid")
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id,
            expected_origin=settings.frontend_base_url,
            credential_public_key=passkey.public_key,
            # Synced passkeys (iCloud Keychain, Google Password Manager) always report 0, which is accepted.
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        raise PasskeyError("passkey_invalid") from exc
    user = db.get(User, passkey.user_id)
    if user is None or user.status != "active":
        raise PasskeyError("passkey_invalid")
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = datetime.now(UTC)
    db.flush()
    return user


def list_passkeys(db: Session, user_id: uuid.UUID) -> list[WebAuthnCredential]:
    query = select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
    return list(db.scalars(query.order_by(WebAuthnCredential.created_at)))


def remove(db: Session, user_id: uuid.UUID, passkey_id: uuid.UUID) -> bool:
    removed = db.scalar(
        delete(WebAuthnCredential)
        .where(WebAuthnCredential.id == passkey_id, WebAuthnCredential.user_id == user_id)
        .returning(WebAuthnCredential.id)
    )
    found = removed is not None
    if found:
        audit.record(db, "PASSKEY_REMOVED", user_id=user_id, passkey_id=passkey_id)
    return found
