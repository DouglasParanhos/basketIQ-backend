"""The OCR fixtures and the invented photo layouts must hold no personal data (spec 009, A2.2).

Real receipts carry the tax number of the buyer and of the shop, card digits, the name of the person at
the till, dates and times, addresses. A fixture cut from a real photo keeps its item rows only, and this test
fails when anything of that kind is left in one, or in the text of an invented layout."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.golden import photo_layouts

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ocr_photos"

# (what it is, pattern): all are shapes of personal or identifying data, none belongs to an item row.
SHAPES = {
    "a number of nine or more digits (tax number, document, terminal)": r"\d{9,}",
    "a card number, whole or masked": r"(?:\d[ -]?){13,19}|[*xX]{2,}\s?\d{2,4}|\d{4}\s?[*xX]{4}",
    "a tax-number or document label": r"\b(?:NIF|NIPC|Contribuinte|ATCUD|FATURA|FT\s?\d)",
    "a card or payment label": r"\b(?:CART[ÃA]O|Visa|Mastercard|Multibanco|MB\s?WAY|Terminal)\b",
    "a person at the till": r"\b(?:Atendid[oa]|Operador|Caixa\s?\d|Vendedor|Funcion[aá]rio|Cliente|Nome)\b",
    "a person's name (two capitalised words)": (
        r"\b[A-ZÀ-Ý][a-zà-ÿ]{2,}\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]{2,}\b"
    ),
    "a date or a time": r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b",
    "an address, phone or e-mail": r"\b\d{4}-\d{3}\b|\bTEL\b|\bTel[.:]|@|\bRua\b|\bAv(?:enida|\.)\b",
}


def texts_of(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [payload["about"]] + [t["text"] for line in payload["lines"] for t in line]


def offences(texts: list[str]) -> list[str]:
    return [
        f"{label}: {text!r}"
        for text in texts
        for label, pattern in SHAPES.items()
        if re.search(pattern, text)
    ]


@pytest.mark.req("F-49")
@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_an_ocr_fixture_holds_item_rows_only(path: Path) -> None:
    assert offences(texts_of(path)) == []


@pytest.mark.req("F-49")
def test_there_are_fixtures_to_check() -> None:
    assert len(list(FIXTURES.glob("*.json"))) >= 2  # an empty glob would pass the test above for nothing


@pytest.mark.req("F-49")
@pytest.mark.parametrize("layout", photo_layouts.LAYOUTS)
def test_an_invented_layout_holds_no_personal_data(layout: str) -> None:
    rows, _ = photo_layouts.build(layout)
    shapes_of_data = {k: v for k, v in SHAPES.items() if "label" not in k or "tax" in k}
    found = [
        f"{label}: {text!r}"
        for left, right in rows
        for text in (left, right)
        for label, pattern in shapes_of_data.items()
        if re.search(pattern, text)
    ]
    assert found == []


@pytest.mark.req("F-49")
def test_the_check_catches_what_it_is_there_for() -> None:
    samples = [
        "Contribuinte: 123456789",
        "CARTAO: ****9010",
        "Atendido por: Maria Santos",
        "2026-10-18 13:27:17",
        "Rua das Flores 12",
        "FATURA FT 00490107",
    ]
    assert all(offences([sample]) for sample in samples)
    assert offences(["CHUCRUTE, 500G", "2 x 0,10 €", "A PAGAR 97,37 €", "0,298 kg x 1,99 €/kg"]) == []
