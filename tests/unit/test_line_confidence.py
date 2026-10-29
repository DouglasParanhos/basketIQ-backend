from __future__ import annotations

import pytest

from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import COUNT_INFERRED_CONFIDENCE
from basketiq.pipeline.schema import DocumentText, TextLine, TextToken
from tests.helpers import doc_from_lines


def tok(text: str, x0: float, confidence: float) -> TextToken:
    return TextToken(text=text, x0=x0, x1=x0 + 40, y0=0, y1=10, confidence=confidence)


@pytest.mark.req("F-12")
def test_the_vat_class_after_the_amount_does_not_lower_the_line() -> None:
    line = TextLine(tokens=[tok("COSTELETAS DO LOMBO", 0, 0.99), tok("3,23", 600, 0.99), tok("B", 650, 0.56)])
    assert line.confidence == 0.99


@pytest.mark.req("F-12")
def test_a_stray_dash_does_not_lower_the_line() -> None:
    line = TextLine(tokens=[tok("-", 0, 0.29), tok("BIJO", 30, 0.96), tok("0,84 B", 600, 0.96)])
    assert line.confidence == 0.96


@pytest.mark.req("F-12")
def test_a_misread_quantity_or_amount_still_lowers_the_line() -> None:
    quantity = TextLine(tokens=[tok("7", 0, 0.3), tok("BIJO", 30, 0.96), tok("0,84", 600, 0.96)])
    assert quantity.confidence == 0.3
    amount = TextLine(tokens=[tok("BIJO", 0, 0.96), tok("0,84", 600, 0.4), tok("B", 650, 0.9)])
    assert amount.confidence == 0.4


@pytest.mark.req("F-12")
def test_a_single_character_without_an_amount_before_it_counts() -> None:
    assert TextLine(tokens=[tok("B", 0, 0.4), tok("PAO", 50, 0.99)]).confidence == 0.4


@pytest.mark.req("RF-06")
def test_a_lost_count_is_deduced_from_unit_price_and_total() -> None:
    """The unit price divides the total exactly: the count is read, but on a photo a misread digit can still
    divide, so the quantity is reviewed (spec 009, A2.2). The text of a PDF is exact: see the next test."""
    line = TextLine(
        tokens=[tok("-", 0, 0.29), tok("BIJO", 30, 0.96), tok("0,12", 500, 0.96), tok("0,84 B", 600, 0.96)]
    )
    bijo = (
        parser_for(Merchant.MERCADONA)
        .parse(_doc(line, TextLine(tokens=[tok("TOTAL 0,84", 0, 0.99)])))
        .lines[0]
    )
    assert (bijo.raw_description, bijo.quantity, bijo.unit_price_cents, bijo.line_total_cents) == (
        "BIJO",
        7,
        12,
        84,
    )
    assert bijo.field_confidence["quantity"] <= COUNT_INFERRED_CONFIDENCE


@pytest.mark.req("RF-06")
def test_a_lost_count_in_a_pdf_needs_no_confirmation() -> None:
    line = TextLine(
        tokens=[tok("-", 0, 0.29), tok("BIJO", 30, 0.96), tok("0,12", 500, 0.96), tok("0,84 B", 600, 0.96)]
    )
    doc = DocumentText(
        lines=[line, TextLine(tokens=[tok("TOTAL 0,84", 0, 0.99)])],
        route=ProcessingRoute.PDF_TEXT,
        engine="test",
    )
    bijo = parser_for(Merchant.MERCADONA).parse(doc).lines[0]
    assert bijo.quantity == 7
    assert bijo.field_confidence["quantity"] >= 0.8


@pytest.mark.req("RF-06")
def test_a_deduced_count_is_flagged_when_the_amounts_were_read_weakly() -> None:
    line = TextLine(tokens=[tok("BIJO", 30, 0.96), tok("0,12", 500, 0.5), tok("0,84 B", 600, 0.96)])
    bijo = (
        parser_for(Merchant.MERCADONA)
        .parse(_doc(line, TextLine(tokens=[tok("TOTAL 0,84", 0, 0.99)])))
        .lines[0]
    )
    assert bijo.quantity == 7
    assert bijo.field_confidence["quantity"] < 0.8


@pytest.mark.req("RF-06")
def test_two_amounts_that_no_count_explains_are_left_to_review_not_guessed() -> None:
    parsed = parser_for(Merchant.UNKNOWN).parse(doc_from_lines(["BIJO 0,12 0,85 B", "TOTAL 0,85"]))
    assert parsed.lines[0].unresolved


def _doc(*lines: TextLine) -> DocumentText:
    return DocumentText(lines=list(lines), route=ProcessingRoute.LOCAL_OCR, engine="test")


@pytest.mark.req("F-12")
def test_a_weak_leading_one_does_not_flag_the_description_or_the_price() -> None:
    """Without a unit price the count can only be 1, so a "1" read with 0.8 flags nothing."""
    item = TextLine(tokens=[tok("1", 0, 0.8), tok("BAGUETTE", 50, 1.0), tok("0,60 B", 600, 0.93)])
    parsed = parser_for(Merchant.MERCADONA).parse(_doc(item, TextLine(tokens=[tok("TOTAL 0,60", 0, 0.99)])))
    baguette = parsed.lines[0]
    assert baguette.quantity == 1
    assert all(v >= 0.8 for v in baguette.field_confidence.values())


@pytest.mark.req("F-12")
def test_a_weak_price_flags_the_price_and_not_the_description() -> None:
    item = TextLine(tokens=[tok("1", 0, 0.99), tok("BAGUETTE", 50, 0.99), tok("0,60", 600, 0.5)])
    baguette = (
        parser_for(Merchant.MERCADONA)
        .parse(_doc(item, TextLine(tokens=[tok("TOTAL 0,60", 0, 0.99)])))
        .lines[0]
    )
    assert baguette.field_confidence["description"] >= 0.8
    assert baguette.field_confidence["line_total"] < 0.8


@pytest.mark.req("F-12")
def test_a_weak_leading_count_that_is_the_quantity_flags_the_quantity() -> None:
    item = TextLine(
        tokens=[tok("3", 0, 0.4), tok("TOALHITA", 50, 0.99), tok("1,25", 500, 0.99), tok("3,75 B", 600, 0.99)]
    )
    wipes = (
        parser_for(Merchant.MERCADONA)
        .parse(_doc(item, TextLine(tokens=[tok("TOTAL 3,75", 0, 0.99)])))
        .lines[0]
    )
    assert wipes.quantity == 3
    assert wipes.field_confidence["quantity"] < 0.8
    assert wipes.field_confidence["line_total"] >= 0.8
