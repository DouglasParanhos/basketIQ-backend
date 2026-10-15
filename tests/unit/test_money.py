from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.money import multiply_cents, parse_decimal, parse_money_cents, to_base_unit


@pytest.mark.req("PT-7")
@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("3,49", 349),
        ("3.49", 349),  # OCR often reads the comma as a dot
        ("1.234,56", 123456),
        ("(0,60)", -60),
        ("-0,50", -50),
        ("0,05", 5),
        ("16,75€", 1675),
        ("12 EUR", None),
        ("0436", None),
        ("1.234", None),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_money_cents(text: str, cents: int | None) -> None:
    assert parse_money_cents(text) == cents


@pytest.mark.req("PT-7")
def test_parse_decimal_comma_and_dot() -> None:
    assert parse_decimal("0,513") == Decimal("0.513")
    assert parse_decimal("2") == Decimal(2)
    assert parse_decimal("1.5") == Decimal("1.5")
    assert parse_decimal("x") is None


@pytest.mark.req("PT-7")
@pytest.mark.parametrize(
    ("qty", "price", "expected"),
    [("0.513", 619, 318), ("2", 319, 638), ("0.742", 599, 444), ("1.038", 199, 207), ("0.005", 100, 1)],
)
def test_multiply_cents_rounds_half_up(qty: str, price: int, expected: int) -> None:
    assert multiply_cents(Decimal(qty), price) == expected


@pytest.mark.req("PT-7")
def test_no_float_rounding_drift() -> None:
    # 0.1 + 0.2 style errors must not exist: everything is Decimal/int.
    assert sum(multiply_cents(Decimal("0.1"), 10) for _ in range(10)) == 10


@pytest.mark.req("PT-7")
def test_to_base_unit_converts_grams_and_millilitres() -> None:
    assert to_base_unit(Decimal(500), "g") == (Decimal("0.5"), "kg")
    assert to_base_unit(Decimal(330), "ml") == (Decimal("0.33"), "l")
    assert to_base_unit(Decimal(2), None) == (Decimal(2), "un")
