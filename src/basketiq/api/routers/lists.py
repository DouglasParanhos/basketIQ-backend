"""Shopping list (RF-16, specs 015 and 019). Every endpoint answers 404 while SMART_LIST_ENABLED is false."""

from __future__ import annotations

import uuid
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from basketiq.api.deps import ConsentedUser, Db, SettingsDep, Today, csrf_protect
from basketiq.api.errors import ApiError
from basketiq.api.routers.config import AppConfigDep, CatalogDep
from basketiq.api.schemas import (
    EvolutionOut,
    ListArticlesOut,
    ShoppingItemIn,
    ShoppingItemPatch,
    ShoppingListOut,
    ShoppingMerchantIn,
    SuggestionOut,
)
from basketiq.services import analytics, lists


def smart_list_enabled(config: AppConfigDep) -> None:
    """Runs before the session and consent checks, so a list that is off does not exist for anyone."""
    if not config.smart_list_enabled:
        raise ApiError(404, "not_found", "Não encontrado.")


router = APIRouter(
    prefix="/v1/lists", tags=["lists"], dependencies=[Depends(smart_list_enabled), Depends(csrf_protect)]
)


@router.get(
    "/articles",
    response_model=ListArticlesOut,
    summary="Smart list, first increment: products bought in the window, per store",
)
def articles(
    db: Db, settings: SettingsDep, config: AppConfigDep, user: ConsentedUser, today: Today
) -> ListArticlesOut:
    return ListArticlesOut.model_validate(
        analytics.list_articles(db, user.id, today, config.comparison_window_days, settings.timezone)
    )


def _list(db: Db, settings: SettingsDep, user: ConsentedUser) -> ShoppingListOut:
    return ShoppingListOut.model_validate(lists.view(db, user.id, ZoneInfo(settings.timezone)))


@router.get(
    "/current", response_model=ShoppingListOut, summary="The user's shopping list, with its estimates"
)
def current(db: Db, settings: SettingsDep, user: ConsentedUser) -> ShoppingListOut:
    return _list(db, settings, user)


@router.put("/current/merchant", response_model=ShoppingListOut, summary="Choose the supermarket of the list")
def choose_merchant(
    body: ShoppingMerchantIn, db: Db, settings: SettingsDep, user: ConsentedUser
) -> ShoppingListOut:
    lists.set_merchant(db, user.id, body.merchant)
    return _list(db, settings, user)


@router.post(
    "/current/items", response_model=ShoppingListOut, status_code=201, summary="Add a product to the list"
)
def add_item(
    body: ShoppingItemIn, db: Db, settings: SettingsDep, catalog: CatalogDep, user: ConsentedUser
) -> ShoppingListOut:
    lists.add_item(db, user.id, catalog, **body.model_dump())
    return _list(db, settings, user)


@router.patch(
    "/current/items/{item_id}", response_model=ShoppingListOut, summary="Edit, tick or mark «já tenho»"
)
def edit_item(
    item_id: uuid.UUID,
    body: ShoppingItemPatch,
    db: Db,
    settings: SettingsDep,
    catalog: CatalogDep,
    user: ConsentedUser,
) -> ShoppingListOut:
    lists.update_item(db, user.id, catalog, item_id, body.model_dump(exclude_unset=True))
    return _list(db, settings, user)


@router.delete(
    "/current/items/{item_id}", response_model=ShoppingListOut, summary="Remove an item (can be undone)"
)
def remove_item(item_id: uuid.UUID, db: Db, settings: SettingsDep, user: ConsentedUser) -> ShoppingListOut:
    lists.remove_item(db, user.id, item_id)
    return _list(db, settings, user)


@router.post(
    "/current/items/{item_id}/restore", response_model=ShoppingListOut, summary="Undo the removal of an item"
)
def restore_item(item_id: uuid.UUID, db: Db, settings: SettingsDep, user: ConsentedUser) -> ShoppingListOut:
    lists.restore_item(db, user.id, item_id)
    return _list(db, settings, user)


@router.get(
    "/current/items/{item_id}/evolution",
    response_model=EvolutionOut,
    summary="What was paid for an item over time",
)
def evolution(
    item_id: uuid.UUID,
    db: Db,
    settings: SettingsDep,
    user: ConsentedUser,
    today: Today,
    months: Annotated[int, Query(ge=1, le=12)] = 6,
) -> EvolutionOut:
    return EvolutionOut.model_validate(
        lists.evolution(db, user.id, item_id, months, today, ZoneInfo(settings.timezone))
    )


@router.get("/current/suggestions", response_model=list[SuggestionOut], summary="«Para a próxima compra»")
def suggestions(
    db: Db,
    settings: SettingsDep,
    config: AppConfigDep,
    catalog: CatalogDep,
    user: ConsentedUser,
    today: Today,
) -> list[SuggestionOut]:
    found = lists.suggestions(db, user.id, catalog, today, config.comparison_window_days, settings.timezone)
    return [SuggestionOut.model_validate(s) for s in found]
