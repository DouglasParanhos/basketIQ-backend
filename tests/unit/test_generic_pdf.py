"""The generic parser on searchable PDFs it has no calibration for (spec 009, A2.1).

Shapes come from the real Continente, Pingo Doce and Corte Inglês PDFs, with invented products."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.status import Merchant
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.table import is_header_row
from basketiq.pipeline.schema import DocumentText, ParsedReceipt
from tests.helpers import doc_from_cells, doc_from_lines


def parse(lines: list[str]) -> ParsedReceipt:
    return parser_for(Merchant.UNKNOWN).parse(doc_from_lines(lines))


def parse_doc(doc: DocumentText) -> ParsedReceipt:
    return parser_for(Merchant.UNKNOWN).parse(doc)


# --- the total (FR-007) ---------------------------------------------------------------------------

# Prices as on the shelf; the savings come off afterwards (Pingo Doce). Gross 20,62, net 13,65.
GROSS_PRINTED = [
    "Artigos",
    "MERCEARIA",
    " C CHOCOLATE LEITE 135G 2,65",
    "      Poupanca Imediata (0,67)",
    " C CAFE MOIDO 250G 3 X 5,99 17,97",
    "      Poupanca Imediata (6,30)",
    "Resumo",
    "TOTAL 20,62",
    "TOTAL POUPANCA (6,97)",
    "TOTAL A PAGAR 13,65",
    "Pagamentos",
]


@pytest.mark.req("F-34")
def test_card_balance_used_as_payment_does_not_reduce_the_total() -> None:
    parsed = parse(
        [
            "IVA DESCRICAO VALOR",
            "Mercearia:",
            "(A) CAFE TORRADO 250G",
            "3 X 2,69 8,07",
            "SUBTOTAL 8,07",
            "Desconto Cartao Utilizado 8,07",
            "TOTAL A PAGAR 0,00",
        ]
    )
    assert parsed.total_cents == 807


@pytest.mark.req("F-34")
def test_gross_and_net_both_fit_the_lines_and_what_was_paid_decides() -> None:
    net = parse([*GROSS_PRINTED, "TOTAL PAGO 13,65", "Multibanco 13,65"])
    assert net.total_cents == 1365
    # The same lines, but the customer paid the gross amount: the payments, not a fixed preference, decide.
    gross = parse([*GROSS_PRINTED, "TOTAL PAGO 20,62", "Multibanco 20,62"])
    assert gross.total_cents == 2062


@pytest.mark.req("F-34")
def test_card_balance_and_card_add_up_to_the_total() -> None:
    parsed = parse(
        [
            "(C) SHAMPOO ANTIQUEDA 250",
            "2 X 2,99 5,98",
            "POUPANCA 4,00",
            "(A) QUEIJO CABRA 150G 2,19",
            "SUBTOTAL 8,17",
            "Desconto Cartao Utilizado 5,00",
            "TOTAL A PAGAR 3,17",
            "Cartao Credito 3,17",
        ]
    )
    assert parsed.total_cents == 817


@pytest.mark.req("F-34")
def test_payments_decide_when_no_printed_total_fits_the_lines() -> None:
    parsed = parse([" C PAO 2,50", " C LEITE 2,50", "TOTAL 7,00", "TOTAL A PAGAR 6,00", "Multibanco 6,00"])
    assert parsed.total_cents == 600


@pytest.mark.req("F-34")
def test_payments_decide_when_there_are_no_readable_lines() -> None:
    parsed = parse(["TOTAL 7,00", "TOTAL A PAGAR 6,00", "Multibanco 6,00"])
    assert parsed.lines == [] and parsed.total_cents == 600


@pytest.mark.req("F-34")
def test_a_total_that_nothing_backs_is_left_empty_never_computed() -> None:
    parsed = parse([" C PAO 2,50", " C LEITE 2,50", "TOTAL 7,00", "TOTAL A PAGAR 6,00"])
    assert parsed.total_cents is None  # the lines add up to 5,00: that is not a printed total either


@pytest.mark.req("F-34")
def test_a_single_printed_total_stays_when_a_line_was_misread() -> None:
    parsed = parse([" C PAO 4,55", " C LEITE 4,56", "TOTAL 9,10"])
    assert parsed.total_cents == 910  # the lines add up to 9,11: the receipt is reviewed, the total is kept


# --- line shapes ----------------------------------------------------------------------------------


@pytest.mark.req("F-35")
def test_description_above_its_quantity_and_prices_is_one_item_and_titles_are_skipped() -> None:
    parsed = parse(
        [
            "Fatura Simplificada Original",
            "IVA DESCRICAO VALOR",
            "Charcutaria&Queijos:",
            "(A) CAFE TORRADO 250G",
            "3 X 2,69 8,07",
            "Padaria:",
            "(A) BROA DE MILHO 400G 1,89",
            "SUBTOTAL 9,96",
        ]
    )
    coffee, broa = parsed.lines
    assert (coffee.raw_description, coffee.quantity, coffee.unit_price_cents, coffee.line_total_cents) == (
        "CAFE TORRADO 250G",
        Decimal(3),
        269,
        807,
    )
    assert coffee.tax_code == "A" and not coffee.unresolved
    assert (broa.raw_description, broa.line_total_cents) == ("BROA DE MILHO 400G", 189)
    assert all(not line.unresolved for line in parsed.lines)


@pytest.mark.req("F-35")
def test_a_column_header_does_not_end_the_items() -> None:
    """ "IVA DESCRICAO VALOR" starts with a word that closes the items in the summary ("IVA 6% ...")."""
    assert is_header_row("IVA DESCRICAO VALOR")
    assert not is_header_row("IVA 6,00% 1,30 0,08")
    assert not is_header_row("TAXA 6,00%")
    assert not is_header_row("Multibanco 8,16")


# --- savings --------------------------------------------------------------------------------------


@pytest.mark.req("F-36")
def test_prices_printed_net_of_the_saving_are_brought_to_gross_and_discount() -> None:
    parsed = parse(
        [
            "(C) SHAMPOO ANTIQUEDA 250",
            "2 X 2,99 5,98",
            "POUPANCA 4,00",
            "(A) QUEIJO CABRA 150G 2,19",
            "SUBTOTAL 8,17",
            "Desconto Cartao Utilizado 5,00",
            "TOTAL A PAGAR 3,17",
            "Cartao Credito 3,17",
        ]
    )
    shampoo, cheese = parsed.lines
    assert (shampoo.quantity, shampoo.unit_price_cents, shampoo.line_total_cents, shampoo.discount_cents) == (
        Decimal(2),
        499,
        998,
        400,
    )
    assert (cheese.line_total_cents, cheese.discount_cents) == (219, 0)


@pytest.mark.req("F-36")
def test_prices_printed_gross_keep_the_shelf_price_and_the_discount() -> None:
    parsed = parse([*GROSS_PRINTED, "TOTAL PAGO 13,65", "Multibanco 13,65"])
    chocolate, coffee = parsed.lines
    assert (chocolate.line_total_cents, chocolate.discount_cents) == (265, 67)
    assert (coffee.quantity, coffee.line_total_cents, coffee.discount_cents) == (Decimal(3), 1797, 630)


@pytest.mark.req("F-36")
def test_a_saving_printed_before_its_item_belongs_to_that_item() -> None:
    parsed = parse(
        [
            "LATICINIOS",
            "      Poupanca Imediata (0,50)",
            " C IOGURTE GREGO 2,19",
            " C ARROZ AGULHA 1,39",
            "TOTAL 3,58",
            "TOTAL A PAGAR 3,08",
            "Multibanco 3,08",
        ]
    )
    yogurt, rice = parsed.lines
    assert (yogurt.line_total_cents, yogurt.discount_cents) == (219, 50)
    assert (rice.line_total_cents, rice.discount_cents) == (139, 0)
    assert parsed.total_cents == 308


@pytest.mark.req("F-36")
def test_lines_are_not_rewritten_when_no_reading_reaches_the_total() -> None:
    parsed = parse(["(A) CAFE TORRADO 250G", "2 X 2,00 4,00", "POUPANCA 1,00", "SUBTOTAL 9,99"])
    (coffee,) = parsed.lines
    assert (coffee.line_total_cents, coffee.discount_cents) == (400, 100)  # as printed, and reviewed
    assert parsed.total_cents == 999


# --- column table ---------------------------------------------------------------------------------

# The Corte Inglês PDF: the header is on three rows (the words of a column sit one above the other).
ELECTRONIC_INVOICE = [
    [("Preço", 424, 449), ("Tipo", 507, 526)],
    [("Produtos", 88, 127), ("Quantidade", 572, 621), ("TOTAL", 662, 692)],
    [("unitário", 420, 453), ("imposto", 499, 534)],
    [
        ("MOLHO", 88, 121),
        ("PICANTE", 124, 163),
        ("5,99", 426, 445),
        ("€", 447, 453),
        ("C", 515, 521),
        ("1", 595, 601),
        ("5,99", 686, 709),
        ("€", 712, 717),
    ],
    [
        ("CHA", 88, 107),
        ("VERDE", 110, 140),
        ("2,00", 426, 445),
        ("€", 447, 453),
        ("C", 515, 521),
        ("2", 595, 601),
        ("4,00", 686, 709),
        ("€", 712, 717),
    ],
    [("TOTAL", 616, 650), ("9,99", 680, 705), ("€", 708, 714)],
]


@pytest.mark.req("F-37")
def test_header_on_several_rows_gives_the_columns() -> None:
    parsed = parse_doc(doc_from_cells(ELECTRONIC_INVOICE))
    sauce, tea = parsed.lines
    assert (sauce.raw_description, sauce.quantity, sauce.unit_price_cents, sauce.line_total_cents) == (
        "MOLHO PICANTE",
        Decimal(1),
        599,
        599,
    )
    assert (tea.raw_description, tea.quantity, tea.unit_price_cents, tea.line_total_cents) == (
        "CHA VERDE",
        Decimal(2),
        200,
        400,
    )
    assert sauce.tax_code == "C" and not sauce.unresolved and not tea.unresolved
    assert parsed.total_cents == 999


@pytest.mark.req("F-37")
def test_other_header_words_and_column_order() -> None:
    """Quantity before the price, the VAT class last, and a sign stuck to the amount ("1,29€")."""
    rows = [
        [("Artigo", 10, 40), ("Qtd", 100, 115), ("P.Unit", 130, 160), ("Total", 190, 210), ("IVA", 235, 247)],
        [
            ("PAO", 10, 30),
            ("DE", 32, 40),
            ("FORMA", 42, 70),
            ("2", 108, 115),
            ("1,29€", 135, 160),
            ("2,58€", 190, 215),
            ("A", 240, 247),
        ],
        [("LEITE", 10, 40), ("6", 108, 115), ("0,79€", 135, 160), ("4,74€", 190, 215), ("A", 240, 247)],
        [("TOTAL", 150, 180), ("7,32", 190, 215)],
    ]
    pao, leite = parse_doc(doc_from_cells(rows)).lines
    assert (pao.raw_description, pao.quantity, pao.unit_price_cents, pao.line_total_cents) == (
        "PAO DE FORMA",
        Decimal(2),
        129,
        258,
    )
    assert (leite.quantity, leite.unit_price_cents, leite.line_total_cents) == (Decimal(6), 79, 474)


@pytest.mark.req("F-37")
def test_a_row_the_table_cannot_read_is_kept_for_review() -> None:
    rows = [row for row in ELECTRONIC_INVOICE[:4]] + [
        [("BRINDE", 88, 120), ("1", 595, 601)],
        [("TOTAL", 616, 650), ("5,99", 680, 705)],
    ]
    sauce, gift = parse_doc(doc_from_cells(rows)).lines
    assert not sauce.unresolved
    assert gift.unresolved and "BRINDE" in gift.raw_description


@pytest.mark.req("F-37")
def test_without_a_header_the_lines_are_read_by_their_shape() -> None:
    parsed = parse(
        ["BANANAS 1,200 X 1,49 1,79", "LIMOES 3 X 0,35 1,05", "TOTAL 2,84", "Numerario 5,00", "Troco 2,16"]
    )
    assert [(x.raw_description, x.line_total_cents) for x in parsed.lines] == [
        ("BANANAS", 179),
        ("LIMOES", 105),
    ]
    assert parsed.total_cents == 284  # the cash handed over is not the total
