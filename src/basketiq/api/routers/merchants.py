"""The merchants the app reads, with the name to show (RF-13). The API is the only source of these names."""

from __future__ import annotations

from fastapi import APIRouter

from basketiq.api.deps import CurrentUser
from basketiq.api.schemas import MerchantOut
from basketiq.domain.status import MERCHANT_NAMES, SUPPORTED_MERCHANTS

router = APIRouter(prefix="/v1/merchants", tags=["merchants"])


@router.get("", response_model=list[MerchantOut], summary="Supported merchants, in a fixed order")
def list_merchants(_: CurrentUser) -> list[MerchantOut]:
    return [MerchantOut(id=m.value, name=MERCHANT_NAMES[m]) for m in SUPPORTED_MERCHANTS]
