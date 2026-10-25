"""The generic parser on layouts it has no calibration for (shapes taken from real receipts)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.generic_pt import CONFIDENCE_CAP
from basketiq.pipeline.schema import ParsedReceipt
from tests.helpers import doc_from_lines


def parse(lines: list[str]) -> ParsedReceipt:
    return parser_for(Merchant.UNKNOWN).parse(doc_from_lines(lines))


# Quantity line BEFORE the item it explains, euro sign, VAT class as a digit, discount with a trailing minus.
CORNER_SHOP = [
    "FRUTARIA DO ZE",
    "Rua das Flores 12",
    "FATURA",
    "DENOMINAÇÃO PREÇO IVA",
    "BRETZELS 868G 2,99 € 1",
    "6 x 0,99 €",
    "LEITE UHT GORDO, 1L 5,94 € 1",
    "0,298 kg x 1,99 €/kg",
    "PEPINO 0,59 € 1",
    "IOGURTE GREGO 2,25 € 1",
    "DESCONTO 0,46-€",
    "A PAGAR 8,32 €",
    "PAGAMENTO COM CART 8,32 €",
    "ALDEIA NOVA",
    "LISBOA",
    "Terminal Pagamento Automático: 01053442",
    "2026-10-08 19:56:36",
]


@pytest.mark.req("RF-06")
def test_quantity_line_before_the_item_is_matched_by_arithmetic() -> None:
    parsed = parse(CORNER_SHOP)
    bretzels, milk, cucumber, yogurt = parsed.lines
    assert (bretzels.quantity, bretzels.line_total_cents) == (Decimal(1), 299)
    assert (milk.quantity, milk.unit, milk.unit_price_cents, milk.line_total_cents) == (
        Decimal(6),
        "un",
        99,
        594,
    )
    assert (cucumber.quantity, cucumber.unit, cucumber.unit_price_cents) == (Decimal("0.298"), "kg", 199)
    assert yogurt.discount_cents == 46
    assert parsed.total_cents == 832


@pytest.mark.req("RF-06")
def test_quantity_line_after_the_item_is_matched_too() -> None:
    parsed = parse(["IOGURTE 4X125G 1,98 B", "2 x 0,99", "A PAGAR 1,98"])
    assert (parsed.lines[0].quantity, parsed.lines[0].unit_price_cents) == (Decimal(2), 99)


@pytest.mark.req("RF-06")
def test_a_quantity_line_that_explains_no_item_does_not_change_one() -> None:
    parsed = parse(["PAO 1,10 B", "7 x 0,12", "LEITE 0,89 B", "A PAGAR 1,99"])
    assert [line.quantity for line in parsed.lines] == [Decimal(1), Decimal(1)]


@pytest.mark.req("RF-06")
def test_header_words_and_the_store_are_not_items_and_the_name_is_guessed() -> None:
    parsed = parse(CORNER_SHOP)
    assert parsed.lines[0].raw_description == "BRETZELS 868G"
    assert parsed.merchant_name == "FRUTARIA DO ZE"
    assert parsed.store_label == "ALDEIA NOVA - LISBOA"


# Count first, unit price and total, weighed items on two lines (Mercadona).
COUNT_FIRST = [
    "SUPERMERCADO ESTRELA",
    "Descrição P. Unit Valor (€)",
    "1 ROLO GIGANTE 2,90 D",
    "3 TOALHITA BEBE AGUA 1,25 3,75 B",
    "24 OVOS FRESCOS 5,75 B",
    "1 COUVE-FLOR",
    "1,004 kg 2,50 €/kg 2,51 B",
    "TOTAL 12,91",
]


@pytest.mark.req("RF-06")
def test_leading_count_is_a_quantity_only_when_it_explains_the_total() -> None:
    parsed = parse(COUNT_FIRST)
    roll, wipes, eggs, cauliflower = parsed.lines
    assert (roll.raw_description, roll.quantity) == ("ROLO GIGANTE", Decimal(1))
    assert (wipes.raw_description, wipes.quantity, wipes.unit_price_cents) == (
        "TOALHITA BEBE AGUA",
        Decimal(3),
        125,
    )
    assert (eggs.raw_description, eggs.quantity) == ("24 OVOS FRESCOS", Decimal(1))  # 24 is part of the name
    assert (cauliflower.raw_description, cauliflower.quantity, cauliflower.unit) == (
        "COUVE-FLOR",
        Decimal("1.004"),
        "kg",
    )
    assert parsed.total_cents == 1291


@pytest.mark.req("RF-06")
def test_everything_the_generic_parser_reads_is_still_reviewed() -> None:
    parsed = parse(COUNT_FIRST)
    assert all(v <= CONFIDENCE_CAP for line in parsed.lines for v in line.field_confidence.values())


@pytest.mark.req("RF-06")
def test_store_label_ignores_payment_lines_before_the_terminal() -> None:
    parsed = parse(["PAO 1,10 B", "A PAGAR 1,10", "TAXA BASE IMP.", "Terminal Pagamento Automático: 1"])
    assert parsed.store_label is None


@pytest.mark.req("RF-06")
def test_a_receipt_without_a_recognisable_name_has_none() -> None:
    assert parse(["NIF: 123456789", "PAO 1,10 B", "A PAGAR 1,10"]).merchant_name is None


@pytest.mark.req("RF-06")
def test_a_price_alone_above_its_description_is_paired_and_reviewed() -> None:
    """Seen on a photo: the OCR read the price of LAVA-LOUCA on a row above the name."""
    parsed = parse(
        ["GEL ROMA 1,60 D", "4,25 D", "E", "LAVA-LOUCA EXPERT", "ESC. MEDIA PACK-4 1,35 D", "TOTAL 7,20"]
    )
    gel, dish, esc = parsed.lines
    assert (dish.raw_description, dish.line_total_cents, dish.unresolved) == ("LAVA-LOUCA EXPERT", 425, False)
    assert (gel.line_total_cents, esc.line_total_cents) == (160, 135)
    assert dish.field_confidence["line_total"] <= 0.7  # a guess: marked for review


@pytest.mark.req("RF-06")
def test_a_price_alone_below_its_description_is_paired_too() -> None:
    parsed = parse(["PAO 1,29 B", "BAGUETTE", "0,60 B", "TOTAL 1,89"])
    assert [(line.raw_description, line.line_total_cents) for line in parsed.lines] == [
        ("PAO", 129),
        ("BAGUETTE", 60),
    ]


@pytest.mark.req("RF-06")
def test_a_price_alone_with_no_description_nearby_stays_unidentified() -> None:
    parsed = parse(["PAO 1,29 B", "0,60 B", "OVOS 2,50 B", "TOTAL 4,39"])
    assert [line.unresolved for line in parsed.lines] == [False, True, False]


@pytest.mark.req("RF-06")
def test_a_row_joined_with_the_next_ones_price_is_not_guessed() -> None:
    parsed = parse(["1 PETINGA TOMATE P2 1,70 8 2,00 B", "PETINGA AZEITE P2 0,60 B", "TOTAL 2,30"])
    assert parsed.lines[0].unresolved and not parsed.lines[1].unresolved


@pytest.mark.req("RF-06")
def test_a_weighed_line_survives_the_ocr_reading_g_as_zero() -> None:
    parsed = parse(["PAO 1,29 B", "1 BANANA", "0,950 kg 1,20 €/k0 1,14 B", "TOTAL 2,43"])
    banana = parsed.lines[1]
    assert (banana.raw_description, banana.quantity, banana.unit, banana.line_total_cents) == (
        "BANANA",
        Decimal("0.950"),
        "kg",
        114,
    )
