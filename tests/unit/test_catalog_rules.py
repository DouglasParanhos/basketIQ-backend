"""Step C catalogue (spec 010): the file loads strictly and every group keeps its «belongs» and «does NOT
belong» cases (D2, D6). Descriptions are loose line descriptions written for the tests, no receipts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from basketiq.catalog import DEFAULT_PATH, CatalogError, CatalogIn, build, load_catalog
from basketiq.domain.grouping import GroupStatus, classify

CATALOG, CASES = load_catalog(DEFAULT_PATH)


def _case(text: str) -> tuple[str | None, str]:
    """«CHAIN | description» reads the description as printed by that chain."""
    chain, sep, description = text.partition(" | ")
    return (chain, description) if sep else (None, text)


def _cases(kind: str) -> list[tuple[str, str]]:
    return [(slug, case) for slug, c in sorted(CASES.items()) for case in getattr(c, kind)]


@pytest.mark.req("F-56")
def test_every_group_has_at_least_three_cases_that_belong_and_one_that_does_not() -> None:
    groups = {g.slug for g in CATALOG.groups}
    assert groups == set(CASES), "every group needs cases"
    for slug, c in CASES.items():
        assert len(c.belongs) >= 3, slug
        assert len(c.not_belongs) >= 1, slug


@pytest.mark.req("F-56")
@pytest.mark.parametrize(("slug", "case"), _cases("belongs"))
def test_a_description_that_belongs_falls_in_its_group(slug: str, case: str) -> None:
    chain, description = _case(case)
    assert classify(description, chain, CATALOG).group == slug


@pytest.mark.req("F-56")
@pytest.mark.parametrize(("slug", "case"), _cases("not_belongs"))
def test_a_description_that_does_not_belong_stays_out_of_the_group(slug: str, case: str) -> None:
    chain, description = _case(case)
    assert classify(description, chain, CATALOG).group != slug


@pytest.mark.req("F-57")
def test_a_description_matching_two_groups_is_left_to_group_never_the_first_rule() -> None:
    raw = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
    raw["classes"].append(
        {
            "slug": "teste",
            "name": "Teste",
            "bases": ["kg"],
            "children": [
                {"slug": "a", "name": "A", "match": [r"\bLEITE\b"]},
                {"slug": "b", "name": "B", "match": [r"\bUHT\b"]},
            ],
        }
    )
    catalog, _ = build(CatalogIn.model_validate(raw))
    result = classify("LEITE UHT MEIO GORDO 1L", None, catalog)
    assert result.status == GroupStatus.UNGROUPED
    assert result.group is None
    assert set(result.candidates) >= {"a", "b", "leite-uht"}


@pytest.mark.req("F-57")
def test_a_description_matching_only_a_node_keeps_it_as_a_hint() -> None:
    result = classify("QUEIJO FETA ORIGINAL 200G", None, CATALOG)
    assert result.status == GroupStatus.UNGROUPED
    assert result.hint == "queijos"
    assert "queijo-fresco" in result.candidates
    assert classify("PARMIGIANO REGGIANO DOP", None, CATALOG) == classify("XYZ", None, CATALOG)


@pytest.mark.req("F-57")
def test_lines_that_are_not_products_are_never_left_to_group() -> None:
    result = classify("DEPÓSITO", None, CATALOG)
    assert result.status == GroupStatus.NOT_COMPARABLE
    assert result.group == "nao-produto"


@pytest.mark.req("F-58")
def test_a_chain_abbreviation_only_applies_to_that_chain() -> None:
    assert classify("MNT MIMOSA LIGHT250G", "PINGO_DOCE", CATALOG).group == "manteiga"
    assert classify("MNT MIMOSA LIGHT250G", "LIDL", CATALOG).group is None
    assert classify("MNT MIMOSA LIGHT250G", None, CATALOG).group is None


@pytest.mark.req("F-58")
def test_the_brand_is_never_part_of_the_group() -> None:
    assert {classify(d, None, CATALOG).group for d in ("LEITE UHT MIMOSA 1L", "LEITE UHT PINGO DOCE 1L")} == {
        "leite-uht"
    }


def _write(tmp_path: Path, raw: dict[str, object]) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.req("F-59")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(unknown=1), "unknown"),
        (lambda r: r["classes"][0]["children"][0]["children"][0].update(match=["("]), "regular expression"),
        (lambda r: r["classes"].append(dict(r["classes"][0])), "repeated slug"),
        (lambda r: r["abbreviations"].update(NOWHERE={"A": "B"}), "unknown chains"),
        (lambda r: r["classes"][0].update(list_category="nowhere"), "unknown list_category"),
        (lambda r: r["classes"][0]["children"][0].update(bases=["caixa"]), "plausible size range"),
        (
            lambda r: r["classes"].append(
                {"slug": "x", "name": "X", "children": [{"slug": "y", "name": "Y"}]}
            ),
            "bases",
        ),
    ],
)
def test_an_invalid_catalogue_stops_the_start(tmp_path: Path, change: object, message: str) -> None:
    raw = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
    change(raw)  # type: ignore[operator]
    with pytest.raises(CatalogError, match=message):
        load_catalog(_write(tmp_path, raw))


@pytest.mark.req("F-59")
def test_a_missing_catalogue_stops_the_start(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "missing.yaml")
