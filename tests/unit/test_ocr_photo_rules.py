"""Layout rules for photographed receipts read by the generic parser (spec 009, A2.2, phase 1): a column
title that is not the end of the items, a VAT code in front of the name, deposit lines, and amounts as the OCR
reads them. They are rules about the layout, none of them knows a shop."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.confidence import Thresholds, evaluate
from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.orchestrator import view_from_parsed
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import COUNT_INFERRED_CONFIDENCE
from basketiq.pipeline.parsers.generic_pt import _one_letter_apart
from basketiq.pipeline.parsers.table import first_column_role, is_header_row
from basketiq.pipeline.schema import ParsedReceipt
from tests.helpers import doc_from_lines


def parse(lines: list[str]) -> ParsedReceipt:
    doc = doc_from_lines(lines, route=ProcessingRoute.LOCAL_OCR)
    return parser_for(Merchant.UNKNOWN).parse(doc)


def names(parsed: ParsedReceipt) -> list[str]:
    return [line.raw_description for line in parsed.lines]


# ---- task 1: the column title and the VAT code ---------------------------------------------------


@pytest.mark.seen_once  # seen on 1 photo: a header with slash-joined titles
@pytest.mark.req("F-46")
def test_a_title_made_of_slash_joined_words_is_a_header() -> None:
    assert is_header_row("IVA ARTIGO/QT/LI PRECO VALOR")
    assert first_column_role("IVA ARTIGO/QT/LI PRECO VALOR") == "tax"
    assert first_column_role("DESCRICAO VALOR") == "desc"
    assert not is_header_row("IVA Liquidado")  # the summary that closes the items
    assert not is_header_row("ARTIGO/FOO")  # one title and one stranger


@pytest.mark.seen_once  # seen on 1 photo: a header with slash-joined titles; the VAT code first
@pytest.mark.req("F-46")
def test_the_header_does_not_close_the_items_and_the_vat_code_is_cut() -> None:
    parsed = parse(
        [
            "IVA ARTIGO/QT/LI PRECO VALOR",
            "Higiene",
            "12 DETERGENTE LOUCA 500ML",
            "X 2,68 5,36",
            "I2 ESCOVA DENTES SOFT 1,40",
            "3 volumes",
            "TOTAL PAGO 6,76",
        ]
    )
    assert names(parsed) == ["DETERGENTE LOUCA 500ML", "ESCOVA DENTES SOFT"]
    assert parsed.total_cents == 676


@pytest.mark.seen_once  # seen on 1 photo: the VAT code first
@pytest.mark.req("F-46")
def test_a_name_that_starts_with_a_digit_or_a_short_token_is_not_cut() -> None:
    parsed = parse(
        [
            "IVA ARTIGO/QT/LI PRECO VALOR",
            "12 PAO DE FORMA 1,00",
            "I2 LEITE MEIO GORDO 0,79",
            "7UP LATA 0,99",  # the code was lost: "7UP" is a name
            "3 CHEFS MOLHO 2,50",  # one character, the codes here have two
            "12 7UP LATA 0,99",  # the code is the first token only
            "TOTAL PAGO 6,07",
        ]
    )
    assert names(parsed) == ["PAO DE FORMA", "LEITE MEIO GORDO", "7UP LATA", "3 CHEFS MOLHO", "7UP LATA"]


@pytest.mark.seen_once  # seen on 1 photo: the VAT code first
@pytest.mark.req("F-46")
def test_nothing_is_cut_without_a_header_that_says_the_vat_code_is_first() -> None:
    no_header = parse(["12 PAO DE FORMA 1,00", "12 LEITE MEIO GORDO 0,79", "TOTAL PAGO 1,79"])
    assert names(no_header) == ["12 PAO DE FORMA", "12 LEITE MEIO GORDO"]
    other_first = parse(
        ["DESCRICAO VALOR", "12 PAO DE FORMA 1,00", "12 LEITE MEIO GORDO 0,79", "TOTAL PAGO 1,79"]
    )
    assert names(other_first) == ["12 PAO DE FORMA", "12 LEITE MEIO GORDO"]
    one_row = parse(["IVA ARTIGO/QT/LI PRECO VALOR", "12 PAO DE FORMA 1,00", "TOTAL PAGO 1,00"])
    assert names(one_row) == ["12 PAO DE FORMA"]  # a single row does not make a column


# ---- task 7: lines that are not products ---------------------------------------------------------


@pytest.mark.req("F-47")
def test_a_deposit_line_is_an_item_and_the_deposit_summary_is_not() -> None:
    parsed = parse(
        [
            "DENOMINACAO PRECO IVA",
            "PAO DE FORMA 500G 1,89 EUR 1",
            "DEPOSITO 0,10 EUR 0",
            "2 x 0,10 EUR",
            "DEPOSITO 0,20 EUR 0",
            "TOTAL DEPOSITO VOLTA* 0,30 EUR",
            "A PAGAR 2,19 EUR",
            "PAGAMENTO COM CARTAO 2,19 EUR",
        ]
    )
    assert names(parsed) == ["PAO DE FORMA 500G", "DEPOSITO", "DEPOSITO"]
    deposit = parsed.lines[2]
    assert (deposit.quantity, deposit.unit_price_cents, deposit.line_total_cents) == (Decimal(2), 10, 20)
    assert sum(line.line_total_cents or 0 for line in parsed.lines) == 219  # the deposits count in the total
    assert parsed.total_cents == 219  # the summary line is not the total of the receipt


@pytest.mark.req("F-47")
def test_payment_and_change_lines_are_not_items() -> None:
    parsed = parse(
        [
            "DESCRICAO VALOR",
            "ARROZ AGULHA 1KG 1,29 A",
            "TOTAL 1,29",
            "Visa 1,29 0,00",
            "TROCO",
            "RESUMO DO IVA",
            "TAXA BASE VALOR",
            "23,00% 1,05 0,24",
        ]
    )
    assert names(parsed) == ["ARROZ AGULHA 1KG"]


# ---- task 8: amounts as the OCR reads them -------------------------------------------------------


@pytest.mark.seen_once  # seen on 1 photo: the euro sign read as a lone e
@pytest.mark.req("F-48")
@pytest.mark.parametrize(
    ("row", "cents"),
    [
        ("CHUCRUTE 500G 1,49 e 3", 149),  # the euro sign read as "e", then the VAT class
        ("MASSAS DE TEMPERO 200G 0.79 e", 79),  # a decimal point, and the same
        ("CENOURA 2KG 1.99 € -", 199),  # a stray dash after the sign
        ("FILETES PEIXE PANADO 3,49 € 3", 349),  # the plain shape still reads
    ],
)
def test_a_misread_euro_sign_or_a_stray_character_does_not_hide_the_price(row: str, cents: int) -> None:
    parsed = parse(["DENOMINACAO PRECO IVA", row, "A PAGAR 9,99 EUR"])
    assert [(line.line_total_cents, line.unresolved) for line in parsed.lines] == [(cents, False)]


@pytest.mark.seen_once  # seen on 1 photo: the euro sign read as a lone e
@pytest.mark.req("F-48")
def test_a_word_e_before_the_price_stays_in_the_name() -> None:
    parsed = parse(["DESCRICAO VALOR", "PAO E LEITE 2,50 A", "TOTAL 2,50"])
    assert names(parsed) == ["PAO E LEITE"]


@pytest.mark.seen_once  # seen on 1 photo: a count the OCR dropped
@pytest.mark.req("F-48")
def test_a_count_the_ocr_dropped_is_worked_out_and_the_line_goes_to_review() -> None:
    parsed = parse(
        ["IVA ARTIGO/QT/LI PRECO VALOR", "12 DETERGENTE LOUCA 500ML", "X 2,68 5,36", "TOTAL PAGO 5,36"]
    )
    (line,) = parsed.lines
    assert (line.quantity, line.unit_price_cents, line.line_total_cents) == (Decimal(2), 268, 536)
    assert line.field_confidence["quantity"] <= COUNT_INFERRED_CONFIDENCE  # never full confidence
    # The generic parser caps every field below the threshold, so the quantity is flagged along with the rest;
    # a parser that trusts the rest of the line (here Aldi's, cap 0.9) shows the reason on its own.
    calibrated = parser_for(Merchant.ALDI).parse(
        doc_from_lines(
            ["DENOMINACAO PRECO IVA", "DETERGENTE LOUCA 500ML", "X 2,68 5,36", "A PAGAR 5,36"],
            route=ProcessingRoute.LOCAL_OCR,
        )
    )
    reason = next(
        r for r in evaluate(view_from_parsed(calibrated, False), Thresholds()).reasons if r.line_index == 0
    )
    assert reason.details["fields"] == ["quantity"]
    assert "deduzida" in reason.message


@pytest.mark.seen_once  # seen on 1 photo: a count the OCR dropped
@pytest.mark.req("F-48")
def test_a_dropped_count_is_not_guessed_when_the_amounts_do_not_divide() -> None:
    parsed = parse(["DESCRICAO VALOR", "ESPONJA COZINHA", "X 1,99 5,00", "TOTAL 5,00"])
    (line,) = parsed.lines
    assert line.unresolved  # 5,00 is not a whole number of 1,99: left for the person, not guessed
    assert (line.raw_description, line.line_total_cents) == ("ESPONJA COZINHA", 500)  # name and total kept


@pytest.mark.req("F-48")
def test_a_weighed_quantity_is_never_worked_out() -> None:
    parsed = parse(["DESCRICAO VALOR", "TOMATE RAMA", "0,412 X 2,49 1,03", "TOTAL 1,03"])
    (line,) = parsed.lines
    assert (line.quantity, line.unit_price_cents, line.line_total_cents) == (Decimal("0.412"), 249, 103)
    assert line.field_confidence["quantity"] > COUNT_INFERRED_CONFIDENCE  # printed, not inferred


@pytest.mark.req("F-48")
def test_a_count_worked_out_by_exact_division_is_reviewed_on_a_photo_but_not_in_a_pdf() -> None:
    rows = ["DESCRICAO VALOR", "BIJO 0,12 0,84 A", "TOTAL 0,84"]
    photo = parser_for(Merchant.UNKNOWN).parse(doc_from_lines(rows, route=ProcessingRoute.LOCAL_OCR))
    pdf = parser_for(Merchant.UNKNOWN).parse(doc_from_lines(rows, route=ProcessingRoute.PDF_TEXT))
    (seen,), (exact,) = photo.lines, pdf.lines
    assert (seen.quantity, exact.quantity) == (Decimal(7), Decimal(7))  # the same reading
    assert seen.field_confidence["quantity"] <= COUNT_INFERRED_CONFIDENCE  # a misread digit can still divide
    assert exact.field_confidence["quantity"] > COUNT_INFERRED_CONFIDENCE  # the text of a PDF is exact


@pytest.mark.req("F-48")
def test_a_printed_count_is_not_reviewed_on_a_photo() -> None:
    rows = ["DESCRICAO VALOR", "3 TOALHITA 1,25 3,75 A", "TOTAL 3,75"]
    parsed = parser_for(Merchant.UNKNOWN).parse(doc_from_lines(rows, route=ProcessingRoute.LOCAL_OCR))
    (line,) = parsed.lines
    assert line.quantity == Decimal(3)
    assert line.field_confidence["quantity"] > COUNT_INFERRED_CONFIDENCE


# ---- commit (h): the weight before its name, a misread discount word, "e/kg" for "€/kg" ------------------

# ---- commit (h): weight before its name, a misread discount word, "e/kg" ----------------------------------


@pytest.mark.seen_once  # seen on 1 photo: the weighed line printed before the name of the item
@pytest.mark.req("F-51")
def test_a_weighed_line_before_its_name_gives_the_item_when_the_arithmetic_closes() -> None:
    parsed = parse(
        [
            "DENOMINACAO PRECO IVA",
            "0,298 kg x 1,99 EUR/kg 0,59 EUR 1",
            "PEPINO",
            "PAO DE FORMA 1,89 EUR 1",
            "A PAGAR 2,48 EUR",
        ]
    )
    cucumber, bread = parsed.lines
    assert (
        cucumber.raw_description,
        cucumber.quantity,
        cucumber.unit_price_cents,
        cucumber.line_total_cents,
    ) == (
        "PEPINO",
        Decimal("0.298"),
        199,
        59,
    )
    assert cucumber.unit == "kg" and bread.raw_description == "PAO DE FORMA"


@pytest.mark.seen_once  # seen on 1 photo: the weighed line printed before the name of the item
@pytest.mark.req("F-51")
def test_a_weighed_line_whose_arithmetic_does_not_close_is_not_given_a_name() -> None:
    parsed = parse(
        ["DENOMINACAO PRECO IVA", "0,298 kg x 1,99 EUR/kg 0,62 EUR 1", "PEPINO", "A PAGAR 0,62 EUR"]
    )
    (line,) = parsed.lines
    assert line.unresolved and line.line_total_cents == 62  # 0,298 x 1,99 is 0,59: left for review
    assert "PEPINO" not in [p.raw_description for p in parsed.lines]


@pytest.mark.seen_once  # seen on 1 photo: the weighed line printed before the name of the item
@pytest.mark.req("F-51")
def test_a_weighed_line_is_never_given_the_name_of_another_item() -> None:
    priced = parse(
        [
            "DENOMINACAO PRECO IVA",
            "0,298 kg x 1,99 EUR/kg 0,59 EUR 1",
            "LEITE MEIO GORDO 1,20 EUR 1",
            "A PAGAR 1,79 EUR",
        ]
    )
    weighed, milk = priced.lines
    assert weighed.unresolved and (milk.raw_description, milk.line_total_cents) == ("LEITE MEIO GORDO", 120)
    named = parse(
        [
            "DENOMINACAO PRECO IVA",
            "0,298 kg x 1,99 EUR/kg 0,59 EUR 1",
            "ATUM EM AZEITE",
            "3 x 2,69 8,07",
            "A PAGAR 8,66 EUR",
        ]
    )
    weighed, tuna = named.lines  # the name has its own prices on the next row
    assert weighed.unresolved and (tuna.raw_description, tuna.quantity, tuna.line_total_cents) == (
        "ATUM EM AZEITE",
        3,
        807,
    )
    alone = parse(["DENOMINACAO PRECO IVA", "0,298 kg x 1,99 EUR/kg 0,59 EUR 1", "A PAGAR 0,59 EUR"])
    assert [line.unresolved for line in alone.lines] == [True]  # no name at all


@pytest.mark.seen_once  # seen on 1 photo: a discount word with a letter misread
@pytest.mark.req("F-52")
def test_a_discount_word_with_one_letter_misread_is_a_discount_when_the_amount_is_negative() -> None:
    parsed = parse(
        ["DENOMINACAO PRECO IVA", "PLANTA SAB MANTEIGA 2,19 EUR 1", "CESCONTO 0.22-EUR", "A PAGAR 1,97 EUR"]
    )
    (plant,) = parsed.lines
    assert (plant.line_total_cents, plant.discount_cents) == (219, 22)


@pytest.mark.seen_once  # seen on 1 photo: a discount word with a letter misread
@pytest.mark.req("F-52")
@pytest.mark.parametrize(
    "row",
    [
        "CESCONTO 0,22 EUR",  # no negative amount: it is not a discount
        "DESCRICAO 0,22-EUR",  # a different word, not one letter apart
        "VALA 1,00-EUR",  # a short word is never forgiven a letter
    ],
)
def test_a_word_near_the_discount_word_is_not_a_discount_without_the_rest(row: str) -> None:
    parsed = parse(["DENOMINACAO PRECO IVA", "PLANTA SAB MANTEIGA 2,19 EUR 1", row, "A PAGAR 2,41 EUR"])
    assert parsed.lines[0].discount_cents == 0


@pytest.mark.seen_once  # seen on 1 photo: a discount word with a letter misread
@pytest.mark.req("F-52")
def test_one_letter_apart() -> None:
    assert _one_letter_apart("CESCONTO", "DESCONTO")  # a letter read as another
    assert _one_letter_apart("DESCONTOS", "DESCONTO")  # a letter more
    assert _one_letter_apart("ESCONTO", "DESCONTO")  # a letter less
    assert not _one_letter_apart("DESCONTO", "DESCONTO")  # the exact word needs no help
    assert not _one_letter_apart("CESCONTA", "DESCONTO")  # two letters


@pytest.mark.seen_once  # seen on 1 photo: the euro sign of "€/kg" read as "e"
@pytest.mark.req("F-53")
def test_e_slash_kg_is_the_euro_sign_when_weight_times_price_gives_the_total() -> None:
    parsed = parse(["DENOMINACAO PRECO IVA", "MACA PINK 0,586 kg x 2,99 e/kg 1,75 EUR 1", "A PAGAR 1,75 EUR"])
    (apple,) = parsed.lines
    assert not apple.unresolved
    assert (apple.quantity, apple.unit_price_cents, apple.line_total_cents) == (Decimal("0.586"), 299, 175)


@pytest.mark.seen_once  # seen on 1 photo: the euro sign of "€/kg" read as "e"
@pytest.mark.req("F-53")
def test_e_slash_kg_is_left_for_review_when_the_arithmetic_does_not_close() -> None:
    parsed = parse(["DENOMINACAO PRECO IVA", "MACA PINK 0,586 kg x 2,99 e/kg 2,75 EUR 1", "A PAGAR 2,75 EUR"])
    (apple,) = parsed.lines
    assert apple.unresolved and apple.line_total_cents == 275


@pytest.mark.seen_once  # seen on 1 photo: the euro sign of "€/kg" read as "e"
@pytest.mark.req("F-53")
def test_an_e_inside_a_word_never_becomes_a_euro_sign() -> None:
    parsed = parse(
        ["DENOMINACAO PRECO IVA", "MACA PINK 0,586 kg x 2,99 LEITE/kg 1,75 EUR 1", "A PAGAR 1,75 EUR"]
    )
    assert not any(not line.unresolved and line.quantity == Decimal("0.586") for line in parsed.lines)
