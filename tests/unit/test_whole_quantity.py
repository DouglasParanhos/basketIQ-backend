"""A quantity printed like a weighed one but with a whole value ("4,000 X 0,69", the Pingo Doce bakery) is a
count, and the line is not sent to review for its unit: a scale stops on a whole kilo about once in a thousand
weighings, and then only the unit is wrong, not the amounts (spec 009, FR-010; the D1b alert was withdrawn on
2026-11-06). A unit printed next to the quantity ("1,000 kg") makes it a weight."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.confidence import Thresholds, evaluate
from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.orchestrator import view_from_parsed
from basketiq.pipeline.parsers import ContinenteParser, PingoDoceParser, parser_for
from basketiq.pipeline.schema import ParsedLine, ParsedReceipt
from tests.helpers import doc_from_cells, doc_from_lines
from tests.unit.test_generic_pdf import ELECTRONIC_INVOICE


def parse(lines: list[str], route: ProcessingRoute = ProcessingRoute.PDF_TEXT) -> ParsedReceipt:
    return parser_for(Merchant.UNKNOWN).parse(doc_from_lines(lines, route=route))


def read(line: ParsedLine) -> tuple[Decimal | None, str | None, bool]:
    """Quantity, unit and whether the unit is in doubt."""
    return line.quantity, line.unit, "unit" in line.field_confidence


@pytest.mark.req("F-38")
def test_whole_value_printed_like_a_weight_is_a_count_without_doubt() -> None:
    parsed = parse(["Artigos", " E QUEQUE 4,000 X 0,69 2,76", " E PAO 0,55", "TOTAL 3,31", "Multibanco 3,31"])
    cake, bread = parsed.lines
    assert (cake.unit_price_cents, cake.line_total_cents) == (69, 276)
    assert read(cake) == (Decimal("4.000"), "un", False)
    assert read(bread) == (Decimal(1), "un", False)
    evaluation = evaluate(view_from_parsed(parsed, False), Thresholds())
    assert all("low_confidence:unit" not in flags for flags in evaluation.line_flags.values())


@pytest.mark.req("F-38")
def test_a_real_weight_or_a_printed_kg_is_a_weight_and_a_count_is_a_count() -> None:
    chicken, turkey, water, rolls, cucumber = parse(
        [
            " C FRANGO INTEIRO 1,004 X 6,19 6,21",  # a weight with a fraction
            " C PEITO PERU 1,000 kg X 5,00 5,00",  # a whole weight that says kg
            " C AGUA 3 X 1,15 3,45",  # a count
            " C PAPO SECO 4 un x 0,14 0,56",  # a count that says un
            " C PEPINO 0,59",
            "0,298 kg x 1,99 €/kg",  # a weighed line says kg
            "TOTAL 16,40",
        ]
    ).lines
    assert read(chicken) == (Decimal("1.004"), "kg", False)
    assert read(turkey) == (Decimal("1.000"), "kg", False)
    assert read(water) == (Decimal(3), "un", False)
    assert read(rolls) == (Decimal(4), "un", False)
    assert read(cucumber) == (Decimal("0.298"), "kg", False)


@pytest.mark.req("F-38")
def test_the_two_line_shape_and_a_quantity_line_read_a_whole_value_as_a_count() -> None:
    (two_lines,) = parse(["(A) QUEQUE", "4,000 X 0,69 2,76", "SUBTOTAL 2,76"]).lines
    assert read(two_lines) == (Decimal("4.000"), "un", False)
    (separate,) = parse([" E QUEQUE 2,76", "4,000 x 0,69", "TOTAL 2,76"]).lines
    assert read(separate) == (Decimal("4.000"), "un", False)
    # A count printed before its item, its total lifted onto the count's row (Aldi photo).
    photo = [
        "DENOMINACAO PRECO IVA",
        "PAO 0,50 € 1",
        "4 x 0,14€ 0,56 € 1",
        "PAPO SECO",
        "0,720 kg x 3,49 €/kg 2,51 € 1",
        "BROCOLOS",
        "A PAGAR 3,57",
    ]
    _, rolls, broccoli = parse(photo, ProcessingRoute.LOCAL_OCR).lines
    assert read(rolls) == (Decimal(4), "un", False)
    assert read(broccoli) == (Decimal("0.720"), "kg", False)


@pytest.mark.req("F-38")
def test_a_quantity_column_reads_a_whole_value_as_a_count() -> None:
    def row(name: str, qty: str, unit: str, total: str) -> list[tuple[str, float, float]]:
        return [(name, 88, 130), (unit, 426, 445), ("C", 515, 521), (qty, 595, 601), (total, 686, 709)]

    rows = [*ELECTRONIC_INVOICE[:3], row("QUEQUE", "4,000", "0,69", "2,76"), row("PAO", "2", "0,55", "1,10")]
    cake, bread = parser_for(Merchant.UNKNOWN).parse(doc_from_cells(rows)).lines
    assert read(cake) == (Decimal("4.000"), "un", False)
    assert read(bread) == (Decimal(2), "un", False)


@pytest.mark.req("F-38")
def test_the_calibrated_pingo_doce_parser_reads_the_bakery_as_counts_and_weights_as_kg() -> None:
    doc = doc_from_lines(
        [
            "Artigos",
            "PADARIA",
            " E QUEQUE 4,000 X 0,69 2,76",
            "TALHO",
            " C FRANGO LOMBINHOS 0,513 X 6,19 3,18",
            " C AGUA 3 X 1,15 3,45",
            "Resumo",
            "TOTAL 9,39",
            "TOTAL A PAGAR 9,39",
        ]
    )
    cake, chicken, water = PingoDoceParser().parse(doc).lines
    assert read(cake) == (Decimal("4.000"), "un", False)
    assert read(chicken) == (Decimal("0.513"), "kg", False)
    assert read(water) == (Decimal(3), "un", False)


@pytest.mark.req("F-38")
def test_the_calibrated_continente_parser_reads_a_printed_kg_as_a_weight() -> None:
    doc = doc_from_lines(
        [
            "IVA DESCRICAO VALOR",
            "(A) QUEQUE",
            "4,000 X 0,69 2,76",
            "(A) PEITO PERU FATIADO",
            "1,000 kg X 5,00 5,00",
            "(A) AGUA",
            "3 X 1,15 3,45",
            "SUBTOTAL 11,21",
        ]
    )
    cake, turkey, water = ContinenteParser().parse(doc).lines
    assert read(cake) == (Decimal("4.000"), "un", False)
    assert read(turkey) == (Decimal("1.000"), "kg", False)
    assert read(water) == (Decimal(3), "un", False)
