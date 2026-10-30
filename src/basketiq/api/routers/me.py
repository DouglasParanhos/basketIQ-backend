from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from basketiq.api.deps import ConsentedUser, CurrentUser, Db, SettingsDep, clear_session_cookies, csrf_protect
from basketiq.api.schemas import DeletionResult, MeOut, ProfileOut, ProfilePatch
from basketiq.db.models import User, UserProfile
from basketiq.services import accounts, profiles
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
            household_size=None, merchants=[], goals=[], monthly_goal_cents=None, onboarding_completed_at=None
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
