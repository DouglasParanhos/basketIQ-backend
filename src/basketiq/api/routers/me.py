from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from basketiq.api.deps import CurrentUser, Db, SettingsDep, clear_session_cookies, csrf_protect
from basketiq.api.schemas import DeletionResult, MeOut
from basketiq.db.models import User
from basketiq.services import accounts
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


@router.get("", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return _me(user)


@router.post("/privacy-consent", response_model=MeOut)
def accept_privacy(db: Db, user: CurrentUser) -> MeOut:
    accounts.accept_privacy(db, user)
    return _me(user)


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
