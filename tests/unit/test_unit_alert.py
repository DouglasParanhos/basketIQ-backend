"""A quantity printed like a weighed one but with a whole value ("4,000 X 0,69") puts the unit in doubt:
units or kg? (spec 009, D1b / FR-010). The generic parser flags it; a real weight does not."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.confidence import LineView, ReceiptView, Thresholds, evaluate
from basketiq.domain.status import Merchant
from basketiq.pipeline.orchestrator import view_from_parsed
from basketiq.pipeline.parsers import ContinenteParser, PingoDoceParser, parser_for
from basketiq.pipeline.parsers.base import UNIT_ALERT_CONFIDENCE
from basketiq.pipeline.schema import ParsedLine, ParsedReceipt
from tests.helpers import doc_from_cells, doc_from_lines


def parse(lines: list[str]) -> ParsedReceipt:
    return parser_for(Merchant.UNKNOWN).parse(doc_from_lines(lines))


def unit_confidence(line: ParsedLine) -> float | None:
    return line.field_confidence.get("unit")


@pytest.mark.req("F-38")
def test_whole_value_printed_like_a_weight_asks_units_or_kg() -> None:
    """The Pingo Doce bakery line: 4 cakes at 0,69, printed "4,000 X 0,69" like 0,513 kg at 6,19."""
    parsed = parse(["Artigos", " E QUEQUE 4,000 X 0,69 2,76", " E PAO 0,55", "TOTAL 3,31", "Multibanco 3,31"])
    cake, bread = parsed.lines
    assert (cake.quantity, cake.unit_price_cents, cake.line_total_cents) == (Decimal("4.000"), 69, 276)
    assert unit_confidence(cake) == UNIT_ALERT_CONFIDENCE  # the reading is kept: the unit is what is in doubt
    assert unit_confidence(bread) is None

    # The generic parser caps every field below the threshold, so the unit is flagged along with the rest.
    evaluation = evaluate(view_from_parsed(parsed, False), Thresholds())
    assert "low_confidence:unit" in evaluation.line_flags[0]
    assert "unit" in next(r for r in evaluation.reasons if r.line_index == 0).details["fields"]


@pytest.mark.req("F-38")
def test_a_real_weight_or_a_printed_unit_or_a_count_does_not_ask() -> None:
    parsed = parse(
        [
            " C FRANGO INTEIRO 1,004 X 6,19 6,21",  # a weight with a fraction
            " C PEITO PERU 1,000 kg X 5,00 5,00",  # a weight that says kg
            " C AGUA 3 X 1,15 3,45",  # a count
            "0,298 kg x 1,99 €/kg",  # a weighed line says kg
            " C PEPINO 0,59",
            "TOTAL 15,25",
        ]
    )
    assert all(unit_confidence(line) is None for line in parsed.lines)


@pytest.mark.req("F-38")
def test_the_alert_also_covers_the_two_line_shape_and_a_quantity_line() -> None:
    two_lines = parse(["(A) QUEQUE", "4,000 X 0,69 2,76", "SUBTOTAL 2,76"])
    assert unit_confidence(two_lines.lines[0]) == UNIT_ALERT_CONFIDENCE
    separate = parse([" E QUEQUE 2,76", "4,000 x 0,69", "TOTAL 2,76"])
    assert unit_confidence(separate.lines[0]) == UNIT_ALERT_CONFIDENCE


@pytest.mark.req("F-38")
def test_the_alert_also_covers_a_quantity_column() -> None:
    header = [
        [("Preço", 424, 449), ("Tipo", 507, 526)],
        [("Produtos", 88, 127), ("Quantidade", 572, 621), ("TOTAL", 662, 692)],
        [("unitário", 420, 453), ("imposto", 499, 534)],
    ]

    def row(name: str, qty: str, unit: str, total: str) -> list[tuple[str, float, float]]:
        return [(name, 88, 130), (unit, 426, 445), ("C", 515, 521), (qty, 595, 601), (total, 686, 709)]

    rows = [*header, row("QUEQUE", "4,000", "0,69", "2,76"), row("PAO", "2", "0,55", "1,10")]
    cake, bread = parser_for(Merchant.UNKNOWN).parse(doc_from_cells(rows)).lines
    assert unit_confidence(cake) == UNIT_ALERT_CONFIDENCE
    assert unit_confidence(bread) is None


@pytest.mark.req("F-38")
def test_confirming_the_unit_clears_the_alert_and_other_edits_do_not() -> None:
    def view(edited: frozenset[str]) -> ReceiptView:
        line = LineView(
            raw_description="QUEQUE",
            quantity=Decimal(4),
            line_total_cents=276,
            discount_cents=0,
            field_confidence={"quantity": 0.9, "unit": UNIT_ALERT_CONFIDENCE, "line_total": 0.9},
            edited_fields=edited,
        )
        return ReceiptView(
            merchant="PINGO_DOCE",
            purchase_at=None,
            total_cents=276,
            subtotal_cents=None,
            tax_cents=None,
            field_confidence={},
            lines=[line],
            edited_fields=frozenset({"purchase_at"}),
        )

    (asked,) = [r for r in evaluate(view(frozenset()), Thresholds()).reasons if r.line_index == 0]
    assert asked.details["fields"] == ["unit"] and "unidades ou kg" in asked.message
    quantity_only = evaluate(view(frozenset({"quantity"})), Thresholds())
    assert quantity_only.line_flags[0] == ["low_confidence:unit"]
    confirmed = evaluate(view(frozenset({"unit"})), Thresholds())
    assert 0 not in confirmed.line_flags


@pytest.mark.req("F-38")
def test_the_calibrated_pingo_doce_parser_asks_too_but_not_for_real_weights() -> None:
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
    assert unit_confidence(cake) == UNIT_ALERT_CONFIDENCE
    assert unit_confidence(chicken) is None and unit_confidence(water) is None


@pytest.mark.req("F-38")
def test_the_calibrated_continente_parser_asks_only_when_no_unit_is_printed() -> None:
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
    assert unit_confidence(cake) == UNIT_ALERT_CONFIDENCE
    assert unit_confidence(turkey) is None and unit_confidence(water) is None
