"""Aldi, Mercadona and El Corte Inglés: layouts of real receipts (items only, no customer data)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from basketiq.domain.status import Merchant
from basketiq.pipeline.classify import classify
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import apply_deposit_quantities
from tests.helpers import doc_from_lines

LISBON = ZoneInfo("Europe/Lisbon")

ALDI_LINES = [
    "A L D I",
    "Avenida do Mar/ Avenida D. Afonso H",
    "FATURA",
    "N.º: FT 00490107APT/324479",
    "DENOMINAÇÃO PREÇO IVA",
    "BRETZELS 868G 2,99 € 1",
    "6 x 0,99 €",
    "LEITE UHT GORDO, 1L 5,94 € 1",
    "FILETES DE PEIXE PANAD 3,49 € 3",
    "2 x 3,49 €",
    "FILETES DE PEIXE PANAD 6,98 € 3",
    "SALADA CAMPONESA, 200G 1,19 € 1",
    "0,298 kg x 1,99 €/kg",
    "PEPINO 0,59 € 1",
    "IOGURTE GREGO 10%, 1 2,25 € 1",
    "DESCONTO 0,46-€",
    "BANDIDA SIDRA MAÇA 50C 0,99 € 3",
    "DEPOSITO 0,10 € 0",
    "2 x 0,10 €",
    "DEPOSITO 0,20 € 0",
    "TOTAL DEPOSITO VOLTA* 0,30 €",
    "A PAGAR 21,23 €",
    "Valor depósito 5,20 €",
    "PAGAMENTO COM CART 16,03 €",
    "ALDI ARDEIRA",
    "CHARNECA CAPARICA",
    "Terminal Pagamento Automático: 01053442",
    "2026-10-08 19:56:36 Per041 Tr052 Msg547",
    "TAXA BASE IMP. VALOR IVA TOTAL",
]


@pytest.mark.req("RF-06")
def test_aldi_quantity_lines_come_before_their_items() -> None:
    parsed = parser_for(Merchant.ALDI).parse(doc_from_lines(ALDI_LINES))
    apply_deposit_quantities(parsed.lines)
    by_name = {line.raw_description: line for line in parsed.lines}
    milk = by_name["LEITE UHT GORDO, 1L"]
    assert (milk.quantity, milk.unit_price_cents, milk.line_total_cents) == (Decimal(6), 99, 594)
    fish = [line for line in parsed.lines if line.raw_description == "FILETES DE PEIXE PANAD"]
    assert [(f.quantity, f.line_total_cents) for f in fish] == [(Decimal(1), 349), (Decimal(2), 698)]
    cucumber = by_name["PEPINO"]
    assert (cucumber.quantity, cucumber.unit, cucumber.unit_price_cents) == (Decimal("0.298"), "kg", 199)
    assert by_name["IOGURTE GREGO 10%, 1"].discount_cents == 46


@pytest.mark.req("RF-06")
def test_aldi_deposit_summary_and_refund_are_not_items_and_total_is_a_pagar() -> None:
    parsed = parser_for(Merchant.ALDI).parse(doc_from_lines(ALDI_LINES))
    apply_deposit_quantities(parsed.lines)
    deposits = [line for line in parsed.lines if line.raw_description == "DEPOSITO"]
    assert [(d.quantity, d.line_total_cents) for d in deposits] == [(Decimal(1), 10), (Decimal(2), 20)]
    assert not any(
        "VOLTA" in line.raw_description or "depósito" in line.raw_description for line in parsed.lines
    )
    assert parsed.total_cents == 2123  # the refund of bottles only reduces what is paid
    assert parsed.store_label == "ALDI ARDEIRA - CHARNECA CAPARICA"
    assert parsed.purchase_at == datetime(2026, 10, 8, 19, 56, tzinfo=LISBON)
    assert parsed.parser_version == "aldi-1.0"


MERCADONA_LINES = [
    "MERCADONA",
    "R BENTO GONÇALVES, 1",
    "CORROIOS",
    "TELEFONE: 260000001",
    "Descrição P. Unit Valor (€)",
    "1 ROLO GIGANTE 2,90 D",
    "3 TOALHITA BEBE AGUA 1,25 3,75 B",
    "2 GRAO DE BICO 0,80 1,60 D",
    "7 BIJO 0,12 0,84 B",
    "24 OVOS FRESCOS 5,75 B",
    "1 COUVE-FLOR",
    "1,004 kg 2,50 €/kg 2,51 B",
    "1 BANANA",
    "0,950 kg 1,20 €/kg 1,14 B",
    "TOTAL 16,49",
    "CARTÃO BANCÁRIO 16,49",
    "Terminal Pagamento Automático: 01428454",
    "26-10-24 12:07:18 Per092 Tr037 Msg129",
]


@pytest.mark.req("RF-06")
def test_mercadona_counts_unit_prices_and_weighed_items() -> None:
    parsed = parser_for(Merchant.MERCADONA).parse(doc_from_lines(MERCADONA_LINES))
    names = [line.raw_description for line in parsed.lines]
    assert names == [
        "ROLO GIGANTE",
        "TOALHITA BEBE AGUA",
        "GRAO DE BICO",
        "BIJO",
        "24 OVOS FRESCOS",
        "COUVE-FLOR",
        "BANANA",
    ]
    rolls, wipes, chickpeas, beads, eggs, cauliflower, banana = parsed.lines
    assert (wipes.quantity, wipes.unit_price_cents, wipes.line_total_cents) == (Decimal(3), 125, 375)
    assert (chickpeas.quantity, beads.quantity) == (Decimal(2), Decimal(7))
    assert (rolls.quantity, eggs.quantity) == (Decimal(1), Decimal(1))
    assert (cauliflower.quantity, cauliflower.unit, cauliflower.unit_price_cents) == (
        Decimal("1.004"),
        "kg",
        250,
    )
    assert (banana.quantity, banana.line_total_cents) == (Decimal("0.950"), 114)
    assert parsed.total_cents == 1649
    assert parsed.store_label == "R BENTO GONÇALVES, 1 - CORROIOS"


EL_CORTE_INGLES_PDF = [
    "FATURA",
    "EL CORTE INGLES - LOJA LISBOA",
    "VISITE-NOS EM: WWW.ELCORTEINGLES.PT",
    "Data / Hora: 2026-10-24 16:50:01",
    "Produtos Preçounitário Tipoimposto Quantidade TOTAL",
    "MOLHO PICANTE 5,99 € C 1 5,99 €",
    "BEB. ENERGÉTICA POWE 1,79 € C 2 3,58 €",
    "DEPOSITO VOLTA 0,10 € D 1 0,10 €",
    "TOTAL 9,67 €",
    "Preços com IVA INCLUÍDO",
]
EL_CORTE_INGLES_PHOTO = [
    "EL CORTE INGLES - LOJA LISBOA",
    "FATURA",
    "Descrição Quantidade Valor",
    "PACCHERI RIGA 1 A 2,16",
    "FUSILLI a 2,59",
    "SACOS T-SHIRT 55MICR 1 C 0,10",
    "TOTAL 4.85",
]


@pytest.mark.req("RF-06")
def test_el_corte_ingles_electronic_invoice() -> None:
    parsed = parser_for(Merchant.EL_CORTE_INGLES).parse(doc_from_lines(EL_CORTE_INGLES_PDF))
    apply_deposit_quantities(parsed.lines)
    sauce, drink, deposit = parsed.lines
    assert (sauce.raw_description, sauce.unit_price_cents, sauce.tax_code) == ("MOLHO PICANTE", 599, "C")
    assert (drink.quantity, drink.unit_price_cents, drink.line_total_cents) == (Decimal(2), 179, 358)
    assert (deposit.raw_description, deposit.quantity) == ("DEPOSITO VOLTA", Decimal(1))
    assert parsed.total_cents == 967
    assert parsed.store_label == "LOJA LISBOA"


@pytest.mark.req("RF-06")
def test_el_corte_ingles_photo_with_tax_class_before_the_amount() -> None:
    parsed = parser_for(Merchant.EL_CORTE_INGLES).parse(doc_from_lines(EL_CORTE_INGLES_PHOTO))
    assert [(line.raw_description, line.line_total_cents) for line in parsed.lines] == [
        ("PACCHERI RIGA", 216),
        ("FUSILLI", 259),
        ("SACOS T-SHIRT 55MICR", 10),
    ]
    assert parsed.total_cents == 485  # the OCR prints 4.85 with a dot


@pytest.mark.req("F-09")
@pytest.mark.parametrize(
    ("lines", "merchant"),
    [
        (["A L D I", "ALDI Retail, Unipessoal LDA", "NIF: 516245708"], Merchant.ALDI),
        (["ALDI", "Avenida do Mar"], Merchant.ALDI),
        (["MERCADONA", "R BENTO GONÇALVES, 1"], Merchant.MERCADONA),
        (["IRMADONA SUPERMERCADOS, UNIPESSOAL, LDA.", "NIF: 514038942"], Merchant.MERCADONA),
        (["El Corte Inglés", "EL CORTE INGLES - GRANDES ARMAZENS, S. A."], Merchant.EL_CORTE_INGLES),
        (["CAPITAL SOCIAL € 130.000.000 CONTRIBUINTE N° 501 81O.285"], Merchant.EL_CORTE_INGLES),
        (["CONTRIBUINTE Nº 501.810.285"], Merchant.EL_CORTE_INGLES),
    ],
)
def test_classification_of_the_new_merchants(lines: list[str], merchant: Merchant) -> None:
    assert classify(doc_from_lines(lines)).merchant == merchant


@pytest.mark.req("RF-06")
def test_auchan_store_is_the_line_above_the_phone() -> None:
    auchan = parser_for(Merchant.AUCHAN).parse(
        doc_from_lines(["BENFICA", "TEL: 210000000", "AUCHAN RETAIL PORTUGAL, S.A."])
    )
    assert auchan.store_label == "BENFICA"
