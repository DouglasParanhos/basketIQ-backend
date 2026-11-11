"""Product catalogue file (config/catalog.yaml): classes, groups and their rules (step C, D2a).

The file is the single source of the group rules, versioned and reviewed by commit, with «belongs»
and «does NOT belong» cases next to each group (tested in tests/unit/test_catalog_cases.py). Loading
is strict like the product configuration: an unknown key, a bad regular expression, a repeated slug
or a group without a comparison base stops the process at start-up.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from basketiq.app_config import describe_errors
from basketiq.domain.categories import CATEGORY_IDS
from basketiq.domain.grouping import Catalog, Node
from basketiq.domain.status import Merchant

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "catalog.yaml"
PATH_ENV_VAR = "BASKETIQ_CATALOG_FILE"

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)]


class CatalogError(RuntimeError):
    """The catalogue file is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Cases(_Strict):
    belongs: list[str] = Field(default_factory=list)
    not_belongs: list[str] = Field(default_factory=list)


class NodeIn(_Strict):
    slug: Slug
    name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    bases: list[str] | None = None
    comparable: bool = True
    match: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    cases: Cases | None = None
    list_category: str | None = None  # category of the shopping list; inherited by the nodes below
    children: list[NodeIn] = Field(default_factory=list)


class CatalogIn(_Strict):
    version: int = Field(ge=1)
    plausible_sizes: dict[str, tuple[Decimal, Decimal]]
    abbreviations: dict[str, dict[str, str]]
    classes: list[NodeIn]
    patterns: dict[str, str] = Field(default_factory=dict)  # only to hold YAML anchors used by the rules


def _compile(patterns: list[str], where: str) -> tuple[re.Pattern[str], ...]:
    try:
        return tuple(re.compile(p) for p in patterns)
    except re.error as exc:
        raise CatalogError(f"{where}: invalid regular expression: {exc}") from exc


def build(raw: CatalogIn) -> tuple[Catalog, dict[str, Cases]]:
    """The catalogue and, per group, its test cases."""
    nodes: dict[str, Node] = {}
    cases: dict[str, Cases] = {}

    def add(n: NodeIn, parent: Node | None) -> None:
        if n.slug in nodes:
            raise CatalogError(f"{n.slug}: repeated slug")
        is_group = parent is not None and not n.children
        bases = tuple(n.bases) if n.bases else (parent.bases if parent else ())
        if is_group and n.comparable and not bases:
            raise CatalogError(f"{n.slug}: a comparable group needs bases (its own or inherited)")
        if n.list_category is not None and n.list_category not in CATEGORY_IDS:
            raise CatalogError(f"{n.slug}: unknown list_category {n.list_category!r}")
        unknown = [b for b in bases if b not in raw.plausible_sizes]
        if unknown:
            raise CatalogError(f"{n.slug}: bases without a plausible size range: {unknown}")
        node = Node(
            slug=n.slug,
            name=n.name,
            parent=parent.slug if parent else None,
            class_slug=parent.class_slug if parent else n.slug,
            bases=bases,
            comparable=n.comparable and (parent.comparable if parent else True),
            is_group=is_group,
            match=_compile(n.match, n.slug),
            exclude=_compile(n.exclude, n.slug) + (parent.exclude if parent else ()),
            list_category=n.list_category or (parent.list_category if parent else None),
        )
        nodes[n.slug] = node
        if is_group and n.cases:
            cases[n.slug] = n.cases
        for child in n.children:
            add(child, node)

    for c in raw.classes:
        add(c, None)
    chains = {m.value for m in Merchant}
    bad_chains = [k for k in raw.abbreviations if k != "all" and k not in chains]
    if bad_chains:
        raise CatalogError(f"abbreviations: unknown chains {bad_chains}")
    abbreviations: dict[str | None, dict[str, str]] = {
        (None if chain == "all" else chain): {k.upper(): v.upper() for k, v in table.items()}
        for chain, table in raw.abbreviations.items()
    }
    return Catalog(raw.version, nodes, abbreviations, dict(raw.plausible_sizes)), cases


def catalog_path() -> Path:
    override = os.environ.get(PATH_ENV_VAR)
    return Path(override) if override else DEFAULT_PATH


def load_catalog(path: Path | None = None) -> tuple[Catalog, dict[str, Cases]]:
    path = path or catalog_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return build(CatalogIn.model_validate(data))
    except FileNotFoundError as exc:
        raise CatalogError(f"Catalogue file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"Could not read catalogue file {path}: {exc}") from exc
    except ValidationError as exc:
        raise CatalogError(f"Invalid catalogue in {path.name}: {describe_errors(exc)}") from exc


@lru_cache
def get_catalog() -> Catalog:
    return load_catalog()[0]
