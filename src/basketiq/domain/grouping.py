"""Product groups (step C, D1–D3, D5): which group a confirmed line description belongs to. Pure.

The catalogue is a tree: classes at the top (only for navigation), nodes in between and groups at
the leaves. Only a group is ever compared. A description that matches two groups, or none, is left
«por agrupar» (D2b): never the first rule that happens to match. When it matches only a node above
the groups, that node is kept as a hint for the person who completes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import cached_property

from basketiq.domain.normalization import normalize_key


class GroupStatus(StrEnum):
    GROUPED = "grouped"
    UNGROUPED = "ungrouped"  # «por agrupar»
    NOT_COMPARABLE = "not_comparable"  # deposits, bags, or the person said so


@dataclass(frozen=True)
class Node:
    slug: str
    name: str
    parent: str | None
    class_slug: str
    bases: tuple[str, ...]  # inherited; only meaningful on a group
    comparable: bool
    is_group: bool
    match: tuple[re.Pattern[str], ...]
    exclude: tuple[re.Pattern[str], ...]  # this node's and its ancestors'


@dataclass(frozen=True)
class Classification:
    status: GroupStatus
    group: str | None = None
    hint: str | None = None  # deepest node matched when no single group did
    candidates: tuple[str, ...] = ()  # groups to offer first


@dataclass(frozen=True)
class Catalog:
    version: int
    nodes: dict[str, Node]
    abbreviations: dict[str | None, dict[str, str]]  # chain (None = every chain) → token → expansion
    plausible_sizes: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)  # D9: base → (min, max)

    # The catalogue never changes once loaded, so what is derived from it is worked out once.
    @cached_property
    def groups(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes.values() if n.is_group)

    @cached_property
    def _groups_under(self) -> dict[str, tuple[str, ...]]:
        under: dict[str, list[str]] = {}
        for g in self.groups:
            for slug in self.ancestors(g.slug):
                under.setdefault(slug, []).append(g.slug)
        return {slug: tuple(groups) for slug, groups in under.items()}

    def groups_under(self, slug: str) -> tuple[str, ...]:
        return self._groups_under.get(slug, ())

    @cached_property
    def _expansions(self) -> dict[str | None, dict[str, str]]:
        """Per chain, the global abbreviations with the chain's own on top (the chain's entry wins)."""
        common = self.abbreviations.get(None, {})
        return {chain: {**common, **table} for chain, table in self.abbreviations.items()}

    def expansions(self, chain: str | None) -> dict[str, str]:
        return self._expansions.get(chain, self._expansions.get(None, {}))

    def ancestors(self, slug: str) -> list[str]:
        out: list[str] = []
        parent = self.nodes[slug].parent
        while parent is not None:
            out.append(parent)
            parent = self.nodes[parent].parent
        return out


def expand(description: str, chain: str | None, catalog: Catalog) -> str:
    """Normalised description with the chain's abbreviations expanded (a chain entry beats a global one)."""
    table = catalog.expansions(chain)
    return " ".join(table.get(token, token) for token in normalize_key(description).split())


def _matches(node: Node, text: str) -> bool:
    return any(p.search(text) for p in node.match) and not any(p.search(text) for p in node.exclude)


def classify(description: str, chain: str | None, catalog: Catalog) -> Classification:
    text = expand(description, chain, catalog)
    hits = [n for n in catalog.nodes.values() if n.match and _matches(n, text)]
    groups = [n for n in hits if n.is_group]
    if len(groups) == 1:
        group = groups[0]
        status = GroupStatus.GROUPED if group.comparable else GroupStatus.NOT_COMPARABLE
        return Classification(status, group=group.slug)
    if groups:
        return Classification(GroupStatus.UNGROUPED, candidates=tuple(sorted(g.slug for g in groups)))
    nodes = [n for n in hits if not n.is_group]
    if not nodes:
        return Classification(GroupStatus.UNGROUPED)
    deepest = max(nodes, key=lambda n: len(catalog.ancestors(n.slug)))
    return Classification(
        GroupStatus.UNGROUPED, hint=deepest.slug, candidates=catalog.groups_under(deepest.slug)
    )
