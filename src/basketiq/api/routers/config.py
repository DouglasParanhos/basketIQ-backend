"""Public product configuration for the client (customer-facing values only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from basketiq.app_config import AppConfig, get_app_config

router = APIRouter(prefix="/v1/config", tags=["config"])

AppConfigDep = Annotated[AppConfig, Depends(get_app_config)]


class PublicConfigOut(BaseModel):
    app_name: str


# Colours and icon paths of the installed app. The icons are the Frontend's public/icons files.
MANIFEST_STATIC: dict[str, Any] = {
    "description": "As suas compras de supermercado, lidas a partir das notas.",
    "lang": "pt-PT",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#EEF2F7",
    "theme_color": "#1D4E9E",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}


@router.get("", response_model=PublicConfigOut, summary="Customer-facing configuration (no sign-in needed)")
def public_config(config: AppConfigDep) -> PublicConfigOut:
    return PublicConfigOut(app_name=config.app_name_for_customer)


@router.get("/manifest.webmanifest", summary="Web app manifest with the configured product name")
def manifest(config: AppConfigDep) -> JSONResponse:
    name = config.app_name_for_customer
    return JSONResponse(
        {"name": name, "short_name": name, **MANIFEST_STATIC}, media_type="application/manifest+json"
    )
