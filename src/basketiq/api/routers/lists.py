"""Smart list (RF-16, spec 015). Every endpoint answers 404 while SMART_LIST_ENABLED is false."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from basketiq.api.deps import ConsentedUser, Db, SettingsDep, Today
from basketiq.api.errors import ApiError
from basketiq.api.routers.config import AppConfigDep
from basketiq.api.schemas import ListArticlesOut
from basketiq.services import analytics


def smart_list_enabled(config: AppConfigDep) -> None:
    """Runs before the session and consent checks, so a list that is off does not exist for anyone."""
    if not config.smart_list_enabled:
        raise ApiError(404, "not_found", "Não encontrado.")


router = APIRouter(prefix="/v1/lists", tags=["lists"], dependencies=[Depends(smart_list_enabled)])


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
