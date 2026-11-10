"""The two rules of the shopping list that can change on their own: the category and the expected price."""

from __future__ import annotations

from datetime import date

import pytest

from basketiq.domain.categories import CATEGORY_IDS, categorize
from basketiq.domain.expected_price import PricePoint, expected_price


@pytest.mark.req("F-311")
@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Leite meio-gordo", "laticinios_ovos"),
        ("OVOS CLASSE M", "laticinios_ovos"),
        ("Pão de forma", "padaria"),
        ("Arroz agulha", "mercearia"),
        ("Papel higiénico", "higiene"),
        ("Detergente loiça", "limpeza"),
        ("Peito de frango", "talho"),
        ("Bananas", "frutas_legumes"),
        ("Água com gás", "bebidas"),
        ("Fraldas", "bebe"),
        ("Qualquer coisa estranha", "outros"),
        ("", "outros"),
    ],
)
def test_the_category_comes_from_the_words_of_the_name(name: str, category: str) -> None:
    assert categorize(name) == category
    assert category in CATEGORY_IDS


@pytest.mark.req("F-311")
def test_a_word_inside_another_word_does_not_decide_the_category() -> None:
    assert categorize("Salada de frutas") != "mercearia"  # «sal» is not «salada»


def point(cents: int, day: int, merchant: str | None, name: str) -> PricePoint:
    return PricePoint(cents, "un", date(2026, 11, day), merchant, name)


@pytest.mark.req("F-312")
def test_the_chosen_supermarket_wins_even_when_its_price_is_older_or_higher() -> None:
    ladder = {
        ("LIDL", "Lidl"): point(500, 1, "LIDL", "Lidl"),
        ("CONTINENTE", "Continente"): point(300, 9, "CONTINENTE", "Continente"),
    }
    found = expected_price("LIDL", ladder)
    assert found is not None
    assert (found[0].cents, found[1]) == (500, False)


@pytest.mark.req("F-312")
def test_without_the_chosen_supermarket_the_most_recent_price_anywhere_is_used_and_flagged() -> None:
    ladder = {
        ("LIDL", "Lidl"): point(500, 1, "LIDL", "Lidl"),
        (None, "Mercearia do Zé"): point(450, 9, None, "Mercearia do Zé"),
    }
    found = expected_price("ALDI", ladder)
    assert found is not None
    assert (found[0].cents, found[0].merchant_name, found[1]) == (450, "Mercearia do Zé", True)


@pytest.mark.req("F-312")
def test_a_store_that_is_not_supported_is_never_the_chosen_one() -> None:
    found = expected_price(None, {(None, "Mercearia do Zé"): point(450, 9, None, "Mercearia do Zé")})
    assert found is not None
    assert found[1] is True


@pytest.mark.req("F-312")
def test_a_product_never_bought_has_no_price() -> None:
    assert expected_price("LIDL", {}) is None
