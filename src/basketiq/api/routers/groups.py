"""Step C (spec 010): catalogue, a group's comparison between chains, «Por completar» and user rules."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from basketiq.api.deps import ConsentedUser, Db, SettingsDep, Today, csrf_protect
from basketiq.api.errors import ApiError, not_found
from basketiq.api.routers.config import AppConfigDep, CatalogDep
from basketiq.api.schemas import (
    CatalogOut,
    GroupComparisonOut,
    LineRuleOut,
    LineRulesIn,
    LineRulesOut,
    ToCompleteOut,
)
from basketiq.services import groups

router = APIRouter(prefix="/v1", tags=["groups"], dependencies=[Depends(csrf_protect)])


@router.get("/catalog/groups", response_model=CatalogOut, summary="Product classes and their groups")
def catalog(catalog: CatalogDep, _: ConsentedUser) -> CatalogOut:
    return CatalogOut.model_validate(groups.catalog_tree(catalog))


@router.get(
    "/groups/{slug}/comparison",
    response_model=GroupComparisonOut,
    summary="What the user paid for one group in each chain, in the comparison window (D12)",
)
def comparison(
    slug: str,
    db: Db,
    settings: SettingsDep,
    config: AppConfigDep,
    catalog: CatalogDep,
    user: ConsentedUser,
    today: Today,
) -> GroupComparisonOut:
    result = groups.group_comparison(db, user.id, slug, catalog, config, today, settings.timezone)
    if result is None:
        raise not_found("Grupo")
    return GroupComparisonOut.model_validate(result)


@router.get(
    "/to-complete", response_model=ToCompleteOut, summary="«Por completar»: descriptions to group or size"
)
def to_complete(
    db: Db,
    config: AppConfigDep,
    catalog: CatalogDep,
    user: ConsentedUser,
    tab: Annotated[Literal["ungrouped", "missing_size"], Query()] = "ungrouped",
) -> ToCompleteOut:
    return ToCompleteOut.model_validate(
        groups.to_complete(db, user.id, tab, catalog, config.to_complete_page_size)
    )


@router.get(
    "/line-rules", response_model=list[LineRuleOut], summary="«Atribuídos por si»: the user's own rules"
)
def line_rules(db: Db, catalog: CatalogDep, user: ConsentedUser) -> list[LineRuleOut]:
    return [LineRuleOut.model_validate(r) for r in groups.list_rules(db, user.id, catalog)]


@router.put(
    "/line-rules",
    response_model=LineRulesOut,
    summary="Group, size or «não comparável» for descriptions; applies to past and future purchases",
)
def put_line_rules(body: LineRulesIn, db: Db, catalog: CatalogDep, user: ConsentedUser) -> LineRulesOut:
    changes = body.model_dump(exclude_unset=True, exclude={"targets"})  # a field sent as null clears it
    targets = [(t.chain.value if t.chain else None, t.description) for t in body.targets]
    try:
        return LineRulesOut.model_validate(groups.apply_rules(db, user.id, targets, changes, catalog))
    except groups.RuleError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc


@router.delete("/line-rules/{rule_id}", status_code=204, response_class=Response, summary="Remove one rule")
def delete_line_rule(rule_id: uuid.UUID, db: Db, user: ConsentedUser) -> Response:
    if not groups.delete_rule(db, user.id, rule_id):
        raise ApiError(404, "not_found", "Regra não encontrada.")
    return Response(status_code=204)
