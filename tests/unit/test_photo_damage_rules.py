"""What the OCR does to photographed receipts of every shop, as seen on the real samples taken turned, at an
angle, sideways or far away (spec 003, 5b): a VAT letter lost, doubled or misread, a count lost, a unit price
with one misread digit, a price lifted onto the row above. Each rule is read as the receipt prints it."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.money import one_digit_apart
from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import count_from_total, parse_date_candidates
from basketiq.pipeline.parsers.generic_pt import PAIRED_CONFIDENCE
from basketiq.pipeline.schema import ParsedLine, ParsedReceipt
from tests.helpers import doc_from_lines


def read(merchant: Merchant, lines: list[str]) -> ParsedReceipt:
    return parser_for(merchant).parse(doc_from_lines(lines, route=ProcessingRoute.LOCAL_OCR))


def reading(line: ParsedLine) -> tuple[str, Decimal | None, int | None, int | None]:
    return line.raw_description, line.quantity, line.unit_price_cents, line.line_total_cents


@pytest.mark.req("F-48")
def test_one_digit_apart() -> None:
    assert one_digit_apart(268, 260)
    assert not one_digit_apart(268, 268)
    assert not one_digit_apart(299, 245)
    assert not one_digit_apart(99, 199)


@pytest.mark.req("F-48")
@pytest.mark.parametrize(
    ("total", "unit", "count"),
    [
        (536, 268, 2),  # whole division
        (536, 260, 2),  # 2 x 2,68 with the 8 read as 0
        (500, 199, None),  # no whole count one digit away
        (536, 0, None),
    ],
)
def test_a_lost_count_comes_from_the_total(total: int, unit: int, count: int | None) -> None:
    assert count_from_total(total, unit) == count


@pytest.mark.req("F-48")
def test_a_unit_price_with_one_misread_digit_is_taken_from_the_total_and_reviewed() -> None:
    parsed = read(
        Merchant.AUCHAN,
        ["IVA ARTIGO/QT/LI PRECO VALOR", "I2 COLGATE MAX WHITE", "2 X 2,60 5,36", "TOTAL PAGO 5,36"],
    )
    (line,) = parsed.lines
    assert reading(line) == ("COLGATE MAX WHITE", Decimal(2), 268, 536)
    assert line.field_confidence["unit_price"] < 0.8


@pytest.mark.req("F-48")
@pytest.mark.parametrize(
    ("row", "cents"),
    [
        ("BATATA DOCE 1,51 el", 151),  # "€ 1" read as "el"
        ("CEBOLA DOCE 500G 1,99 ei", 199),
        ("1 CAP. RISTRETTO ALUM. 3,75.0", 375),  # the class "B" read as ".0"
        ("TORTILHA BAT C/CEB 2.60 )0", 260),
        ("HUMMUS CLASSIC 1.05 o", 105),
        ("PAO DE FORMA 1,89 c 1", 189),  # the euro sign read as "c"
    ],
)
def test_a_misread_vat_class_does_not_hide_the_price(row: str, cents: int) -> None:
    (line,) = read(Merchant.MERCADONA, ["Descrição P. Unit Valor (€)", row, "TOTAL 9,99"]).lines
    assert (line.unresolved, line.line_total_cents) == (False, cents)


@pytest.mark.req("F-48")
def test_a_count_line_with_the_class_of_the_next_row_still_gives_the_count() -> None:
    parsed = read(
        Merchant.ALDI,
        [
            "DENOMINACAO PRECO IVA",
            "PAO 0,50 € 1",
            "6 x 0,99 € 1",
            "LEITE UHT GORDO 1L 5,94 € 1",
            "A PAGAR 6,44",
        ],
    )
    assert reading(parsed.lines[1]) == ("LEITE UHT GORDO 1L", Decimal(6), 99, 594)


@pytest.mark.req("F-48")
def test_a_price_lifted_onto_the_column_titles_belongs_to_the_first_item_and_is_reviewed() -> None:
    parsed = read(
        Merchant.ALDI, ["DENOMINAÇÃO PREÇO 2,99€1", "BRETZELS 868G", "LEITE 1,00 € 1", "A PAGAR 3,99"]
    )
    bretzels, leite = parsed.lines
    assert reading(bretzels) == ("BRETZELS 868G", Decimal(1), 299, 299)
    assert max(bretzels.field_confidence.values()) <= PAIRED_CONFIDENCE
    assert reading(leite) == ("LEITE", Decimal(1), 100, 100)


@pytest.mark.req("F-48")
def test_a_count_line_with_its_total_before_the_name_gives_the_item() -> None:
    parsed = read(
        Merchant.ALDI,
        [
            "DENOMINACAO PRECO IVA",
            "PAO 0,50 € 1",
            "4 x 0,14€ 0,56 € 1",  # a tilt lifted the total of "PAPO SECO" onto its count line
            "PAPO SECO",
            "0,720 kg x 3,49 €/kg 2,51 € 1",
            "BROCOLOS",
            "A PAGAR 3,57",
        ],
    )
    assert [reading(line) for line in parsed.lines[1:]] == [
        ("PAPO SECO", Decimal(4), 14, 56),
        ("BROCOLOS", Decimal("0.720"), 349, 251),
    ]


@pytest.mark.req("F-48")
def test_el_corte_ingles_photo_rows_without_a_class_letter_or_with_it_twice() -> None:
    parsed = read(
        Merchant.EL_CORTE_INGLES,
        [
            "Descrição Quantidade Valor",
            "PACCHERI RIGA 1 2,16",  # the class letter lost
            "FUSILLI 1 AA 2,59",  # read twice
            "SACOS T-SHIRT 55MICR C 0,10",  # the count lost
            "TOTAL 4,85",
        ],
    )
    assert [(line.raw_description, line.line_total_cents, line.tax_code) for line in parsed.lines] == [
        ("PACCHERI RIGA", 216, None),
        ("FUSILLI", 259, "A"),
        ("SACOS T-SHIRT 55MICR", 10, "C"),
    ]


@pytest.mark.req("F-48")
def test_el_corte_ingles_a_misread_digit_is_settled_by_the_total_of_its_vat_class() -> None:
    parsed = read(
        Merchant.EL_CORTE_INGLES,
        [
            "Descrição Quantidade Valor",
            "PACCHERI RIGA 1 A 2,16",
            "FUSILLI 1 A 2,59",
            "CARIL EM PO S 1 C 4,26",  # 4,25 with the 5 read as 6
            "SACOS T-SHIRT 55MICR 1 C 0,10",
            "TOTAL 9,10",
            "(A) IMP 6,00% 4,48 0,27 4,75",
            "(C) IMP 23,00% 3,54 0,81 4,35",
        ],
    )
    caril = parsed.lines[2]
    assert reading(caril) == ("CARIL EM PO S", Decimal(1), 425, 425)
    assert caril.field_confidence["line_total"] <= 0.5
    assert [line.line_total_cents for line in parsed.lines] == [216, 259, 425, 10]


@pytest.mark.req("F-48")
def test_el_corte_ingles_items_without_their_class_letter_take_the_class_whose_total_they_complete() -> None:
    parsed = read(
        Merchant.EL_CORTE_INGLES,
        [
            "Descrição Quantidade Valor",
            "PACCHERI RIGA 1 A 2,16",
            "FUSILLI 1 A 2,59",
            "CARIL EM PO S 4,26",  # class letter lost and 4,25 read as 4,26
            "SACOS T-SHIRT 55MICR 0,10",  # class letter lost
            "TOTAL 9,10",
            "(A) IMP 6,00% 4,48 0,27 4,75",
            "(C) IMP 23,00% 3,54 0,81 4,35",
        ],
    )
    assert [(line.line_total_cents, line.tax_code) for line in parsed.lines] == [
        (216, "A"),
        (259, "A"),
        (425, "C"),
        (10, "C"),
    ]


@pytest.mark.req("F-48")
def test_el_corte_ingles_e_invoice_photographed_without_its_quantity_or_class() -> None:
    parsed = read(
        Merchant.EL_CORTE_INGLES,
        [
            "Produtos Preçounitário Tipoimposto Quantidade TOTAL",
            "BEB. ENERGETICA POWE 1,79 € C 1,79 €",
            "DEPOSITO VOLTA 0,10 € 0,10 €",  # the class letter lost too
            "TOTAL 1,89 €",
        ],
    )
    assert [reading(line) for line in parsed.lines] == [
        ("BEB. ENERGETICA POWE", Decimal(1), 179, 179),
        ("DEPOSITO VOLTA", Decimal(1), 10, 10),
    ]


@pytest.mark.req("F-48")
def test_pingo_doce_photo_without_the_vat_letter_but_debris_stays_unresolved() -> None:
    parsed = read(
        Merchant.PINGO_DOCE, ["Artigos", "QJ HALLOUMI PDIG200G 2,89", "?? ## 1,20", "Resumo", "TOTAL 4,09"]
    )
    halloumi, debris = parsed.lines
    assert reading(halloumi) == ("QJ HALLOUMI PDIG200G", Decimal(1), 289, 289)
    assert debris.unresolved


@pytest.mark.req("F-48")
def test_lidl_a_misread_or_trailing_character_at_the_vat_class() -> None:
    parsed = read(
        Merchant.LIDL,
        [
            "EUR",
            "Tabua de Queijos Espanhois 3,49 B E",
            "SERRAMEL ROSMANINHO ALGARVE 3,49 8",
            "PAO DE QUEIJO 3,29",  # the class washed out by a glare
            "Total 10,27",
        ],
    )
    assert [(*reading(line), line.tax_code) for line in parsed.lines] == [
        ("Tabua de Queijos Espanhois", Decimal(1), 349, 349, "B"),
        ("SERRAMEL ROSMANINHO ALGARVE", Decimal(1), 349, 349, None),  # "8" for B: no class is guessed
        ("PAO DE QUEIJO", Decimal(1), 329, 329, None),
    ]


@pytest.mark.req("F-48")
def test_continente_a_vat_code_without_its_opening_bracket() -> None:
    parsed = read(
        Merchant.CONTINENTE,
        ["IVA DESCRICAO VALOR", "A) 12 CEREAIS E SEMENTES 550G OROW 2,39", "SUBTOTAL 2,39"],
    )
    assert [(line.raw_description, line.line_total_cents) for line in parsed.lines] == [
        ("12 CEREAIS E SEMENTES 550G OROW", 239)
    ]


@pytest.mark.req("F-48")
def test_dates_as_the_ocr_reads_them() -> None:
    lines = doc_from_lines(["24/0ut/26 16:50", "26--10-24 16:51:19"]).lines
    found = {
        (c.value.isoformat(), c.time_.isoformat() if c.time_ else None) for c in parse_date_candidates(lines)
    }
    assert found == {("2026-10-24", "16:50:00"), ("2026-10-24", "16:51:00")}
