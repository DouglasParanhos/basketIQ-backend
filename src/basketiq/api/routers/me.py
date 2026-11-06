from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, Response

from basketiq.api.deps import (
    PASSKEY_COOKIE,
    ConsentedUser,
    CurrentUser,
    Db,
    SettingsDep,
    clear_passkey_challenge_cookie,
    clear_session_cookies,
    csrf_protect,
    set_passkey_challenge_cookie,
)
from basketiq.api.errors import ApiError, ErrorBody
from basketiq.api.routers.config import AppConfigDep
from basketiq.api.schemas import DeletionResult, MeOut, PasskeyOut, ProfileOut, ProfilePatch
from basketiq.db.models import User, UserProfile, WebAuthnCredential
from basketiq.services import accounts, passkeys, profiles
from basketiq.storage import ObjectStorage, get_storage

router = APIRouter(prefix="/v1/me", tags=["account"], dependencies=[Depends(csrf_protect)])


def _me(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        privacy_accepted_at=user.privacy_accepted_at,
        privacy_version=user.privacy_version,
        current_privacy_version=accounts.PRIVACY_POLICY_VERSION,
        created_at=user.created_at,
    )


def _profile_out(profile: UserProfile | None) -> ProfileOut:
    if profile is None:  # nothing saved yet: an empty profile, not an error
        return ProfileOut(
            household_size=None,
            merchants=[],
            goals=[],
            monthly_goal_cents=None,
            onboarding_completed_at=None,
            passkey_offer_dismissed_at=None,
        )
    return ProfileOut.model_validate(profile, from_attributes=True)


@router.get("", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return _me(user)


@router.post("/privacy-consent", response_model=MeOut)
def accept_privacy(db: Db, user: CurrentUser) -> MeOut:
    accounts.accept_privacy(db, user)
    return _me(user)


@router.get("/profile", response_model=ProfileOut, summary="The person's profile (empty until saved)")
def get_profile(db: Db, user: CurrentUser) -> ProfileOut:
    return _profile_out(profiles.get_profile(db, user.id))


@router.patch("/profile", response_model=ProfileOut, summary="Save the fields sent; omitted ones are kept")
def update_profile(body: ProfilePatch, db: Db, user: ConsentedUser) -> ProfileOut:
    return _profile_out(
        profiles.update_profile(db, user.id, body.model_dump(mode="json", exclude_unset=True))
    )


@router.delete("", response_model=DeletionResult, summary="Delete the account and all its data (RF-12)")
def delete_me(
    response: Response,
    db: Db,
    settings: SettingsDep,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    user: CurrentUser,
) -> DeletionResult:
    result = accounts.delete_account(db, storage, user)
    clear_session_cookies(response, settings)
    return DeletionResult(receipts_deleted=result["receipts"], objects_deleted=result["objects"])


# ---------------------------------------------------------------- passkeys (RF-01, spec 016)


@router.get("/passkeys", response_model=list[PasskeyOut], summary="The person's passkeys")
def list_passkeys(db: Db, user: CurrentUser) -> list[WebAuthnCredential]:
    return passkeys.list_passkeys(db, user.id)


@router.post(
    "/passkeys/options",
    response_model=dict[str, Any],
    summary="Options for navigator.credentials.create(); sets the one-time challenge cookie",
)
def passkey_registration_options(
    response: Response, db: Db, settings: SettingsDep, app_config: AppConfigDep, user: ConsentedUser
) -> dict[str, Any]:
    options, challenge_id = passkeys.registration_options(
        db, settings, user, app_config.app_name_for_customer
    )
    set_passkey_challenge_cookie(response, settings, challenge_id, passkeys.CHALLENGE_SECONDS)
    return options


@router.post(
    "/passkeys",
    status_code=201,
    response_model=PasskeyOut,
    responses={400: {"model": ErrorBody}, 409: {"model": ErrorBody}},
    summary="Register the passkey the phone created (body: the credential as JSON)",
)
def add_passkey(
    response: Response,
    credential: Annotated[dict[str, Any], Body()],
    db: Db,
    settings: SettingsDep,
    user: ConsentedUser,
    challenge_id: Annotated[str | None, Cookie(alias=PASSKEY_COOKIE)] = None,
) -> WebAuthnCredential:
    try:
        passkey = passkeys.register(db, settings, user, credential, challenge_id)
    except passkeys.PasskeyExistsError as exc:
        raise ApiError(409, exc.code, "Esta passkey já está registada.") from exc
    except passkeys.PasskeyError as exc:
        raise ApiError(400, exc.code, "Não foi possível registar a passkey. Tente novamente.") from exc
    clear_passkey_challenge_cookie(response, settings)
    return passkey


@router.delete(
    "/passkeys/{passkey_id}",
    status_code=204,
    response_class=Response,
    responses={404: {"model": ErrorBody}},
    summary="Remove one of the person's passkeys",
)
def remove_passkey(passkey_id: uuid.UUID, db: Db, user: CurrentUser) -> Response:
    if not passkeys.remove(db, user.id, passkey_id):
        raise ApiError(404, "not_found", "Passkey não encontrada.")
    return Response(status_code=204)
