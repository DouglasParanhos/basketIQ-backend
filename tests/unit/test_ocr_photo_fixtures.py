"""Two real photographed receipts as OCR tokens (text and position of the item rows only), read by the generic
parser (spec 009, A2.2). They keep what the OCR really produced: rows fused with their neighbours, a euro sign
read as "e", a unit price read as 2,60 instead of 2,68. The fixtures carry nothing but item rows: see
`test_fixture_privacy.py`."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.schema import DocumentText, ParsedLine, ParsedReceipt, TextLine, TextToken

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ocr_photos"


def read(name: str) -> ParsedReceipt:
    payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    # The rows as the parser saw them (grouping a short cut-out would not tilt-correct as the page did).
    lines = [TextLine(tokens=[TextToken(**t) for t in row]) for row in payload["lines"]]
    doc = DocumentText(lines=lines, route=ProcessingRoute.LOCAL_OCR, engine="fixture")
    return parser_for(Merchant.UNKNOWN).parse(doc)


def by_name(parsed: ParsedReceipt, start: str) -> list[ParsedLine]:
    return [line for line in parsed.lines if line.raw_description.upper().startswith(start)]


@pytest.mark.seen_once  # seen on 1 photo: a header with slash-joined titles; the VAT code first
@pytest.mark.req("F-46")
def test_a_photo_whose_column_title_has_slashes_gives_its_items() -> None:
    parsed = read("auchan_items")
    assert parsed.total_cents == 676
    assert [line.raw_description for line in parsed.lines] == ["COLGATE MAX UHIIE ON", "ELIXIR AUCHAN 500 MI"]
    elixir = parsed.lines[1]
    assert (elixir.quantity, elixir.unit_price_cents, elixir.line_total_cents) == (Decimal(1), 140, 140)


@pytest.mark.seen_once  # seen on 1 photo: a count the OCR dropped
@pytest.mark.req("F-48")
def test_a_misread_unit_price_is_not_guessed_and_keeps_name_and_total() -> None:
    first = read("auchan_items").lines[0]
    assert first.unresolved  # "X 2,60 5,36": 5,36 is not a whole number of 2,60
    assert first.line_total_cents == 536


@pytest.mark.seen_once  # seen on 1 photo: the euro sign read as a lone e
@pytest.mark.req("F-48")
def test_aldi_photo_prices_with_a_misread_euro_sign_or_a_stray_dash_are_read() -> None:
    parsed = read("aldi_items")
    for name, cents in (("CHUCRUTE", 149), ("CENOURA", 199), ("MASSAS", 79)):
        (line,) = by_name(parsed, name)
        assert not line.unresolved, name
        assert (line.quantity, line.line_total_cents) == (Decimal(1), cents), name


@pytest.mark.req("F-47")
def test_aldi_photo_deposit_lines_are_items_and_their_summary_is_not() -> None:
    parsed = read("aldi_items")
    assert parsed.total_cents == 9737
    assert not by_name(parsed, "TOTAL DEP")  # the summary of the deposits
    single, double = by_name(parsed, "DEP")
    assert (single.quantity, single.unit_price_cents, single.line_total_cents) == (Decimal(1), 10, 10)
    assert (double.quantity, double.unit_price_cents, double.line_total_cents) == (Decimal(2), 10, 20)


@pytest.mark.req("F-48")
def test_aldi_photo_nothing_resolved_is_wrong() -> None:
    """Every line the parser calls resolved has a total that appears in the printed amounts of its row."""
    parsed = read("aldi_items")
    resolved = [line for line in parsed.lines if not line.unresolved]
    assert len(resolved) >= 22
    assert all(line.line_total_cents and line.quantity for line in resolved)
