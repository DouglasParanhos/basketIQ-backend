from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from basketiq.domain.status import Merchant, ProcessingRoute
from basketiq.pipeline.classify import classify
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import (
    apply_deposit_quantities,
    choose_purchase_datetime,
    parse_date_candidates,
)
from basketiq.pipeline.parsers.generic_pt import CONFIDENCE_CAP
from basketiq.pipeline.schema import ParsedLine
from tests.helpers import PINGO_LINES, PINGO_TOTAL, doc_from_lines

LISBON = ZoneInfo("Europe/Lisbon")


@pytest.mark.req("RF-06")
def test_pingo_doce_items_weight_multi_and_discount() -> None:
    parsed = parser_for(Merchant.PINGO_DOCE).parse(doc_from_lines(PINGO_LINES))
    assert [line.raw_description for line in parsed.lines] == [
        "LEITE MEIO GORDO PD 1L",
        "PAO DE FORMA",
        "BIFE PERU",
        "SUMO LARANJA",
    ]
    bread, turkey, juice = parsed.lines[1], parsed.lines[2], parsed.lines[3]
    assert bread.discount_cents == 30  # "Poupança Imediata" applies to the previous item
    assert (turkey.quantity, turkey.unit, turkey.unit_price_cents, turkey.line_total_cents) == (
        Decimal("0.450"),
        "kg",
        899,
        405,
    )
    assert (juice.quantity, juice.unit, juice.unit_price_cents) == (Decimal(2), "un", 149)
    assert parsed.total_cents == PINGO_TOTAL
    assert parsed.total_discount_cents == 30
    assert parsed.purchase_at == datetime(2026, 9, 15, 12, 30, tzinfo=LISBON)


@pytest.mark.req("RF-06")
def test_pingo_doce_package_size_is_not_a_quantity() -> None:
    parsed = parser_for(Merchant.PINGO_DOCE).parse(
        doc_from_lines(["Artigos", " E CROQ CARN PD 10X32G 2 X 3,19 6,38", "Resumo", "TOTAL A PAGAR 6,38"])
    )
    line = parsed.lines[0]
    assert line.raw_description == "CROQ CARN PD 10X32G"
    assert line.quantity == 2 and line.unit_price_cents == 319


@pytest.mark.req("RF-06")
def test_inconsistent_quantity_line_gets_low_confidence() -> None:
    parsed = parser_for(Merchant.PINGO_DOCE).parse(
        doc_from_lines(["Artigos", " C ARROZ 2 X 1,00 3,00", "Resumo", "TOTAL A PAGAR 3,00"])
    )
    assert parsed.lines[0].field_confidence["quantity"] < 0.8


@pytest.mark.req("RF-06")
def test_unparseable_priced_line_is_kept_unresolved() -> None:
    parsed = parser_for(Merchant.PINGO_DOCE).parse(
        doc_from_lines(["Artigos", " C LEITE 0,79", "?? ##  1,20", "Resumo", "TOTAL A PAGAR 1,99"])
    )
    assert len(parsed.lines) == 2
    assert parsed.lines[1].unresolved
    assert parsed.lines[1].line_total_cents == 120


@pytest.mark.req("RF-06")
def test_lidl_quantity_and_discount_lines() -> None:
    lines = [
        "LIDL & Cia",
        "Data de Venda: 2026-09-12",
        "EUR",
        "IOGURTE 4X125G 1,98 B",
        "2 x 0,99",
        "BANANA 1,23 B",
        "0,856 kg x 1,44 EUR/kg",
        "TOMATE 2,49 B",
        "Desconto -0,50",
        "Total 5,20",
        "12.09.26 10:15",
    ]
    parsed = parser_for(Merchant.LIDL).parse(doc_from_lines(lines))
    yogurt, banana, tomato = parsed.lines
    assert (yogurt.quantity, yogurt.unit, yogurt.unit_price_cents) == (Decimal(2), "un", 99)
    assert (banana.quantity, banana.unit, banana.unit_price_cents) == (Decimal("0.856"), "kg", 144)
    assert tomato.discount_cents == 50
    assert parsed.total_cents == 520
    assert parsed.purchase_at == datetime(2026, 9, 12, 10, 15, tzinfo=LISBON)


@pytest.mark.req("RF-06")
@pytest.mark.parametrize(
    ("total", "quantity", "low_confidence"), [("0,10", 1, False), ("0,60", 6, False), ("0,25", 3, True)]
)
def test_lidl_deposit_quantity_is_total_over_ten_cents(
    total: str, quantity: int, low_confidence: bool
) -> None:
    lines = ["LIDL & Cia", "EUR", f"Deposito 0.10 {total} F", f"Total {total}"]
    parsed = parser_for(Merchant.LIDL).parse(doc_from_lines(lines))
    apply_deposit_quantities(parsed.lines)
    deposit = parsed.lines[0]
    assert not deposit.unresolved
    assert (deposit.quantity, deposit.unit, deposit.unit_price_cents) == (Decimal(quantity), "un", 10)
    assert (deposit.field_confidence["quantity"] < 0.8) is low_confidence


@pytest.mark.req("RF-06")
def test_deposit_rule_ignores_other_lines_and_unresolved_ones() -> None:
    parsed = parser_for(Merchant.PINGO_DOCE).parse(
        doc_from_lines(["Artigos", " C LEITE 0,79", "Resumo", "TOTAL A PAGAR 0,79"])
    )
    milk = parsed.lines[0]
    before = milk.model_copy(deep=True)
    unresolved = ParsedLine(raw_description="Depósito ?? 0,60", line_total_cents=60, unresolved=True)
    apply_deposit_quantities([milk, unresolved])
    assert milk == before
    assert unresolved.quantity is None


@pytest.mark.req("RF-06")
@pytest.mark.parametrize(
    ("merchant", "lines", "store"),
    [
        (Merchant.LIDL, ["LIDL & Cia - LISBOA - AV.LUSIADA", "EUR", "Total 0,00"], "LISBOA - AV.LUSIADA"),
        (Merchant.LIDL, ["LIDL & Cia", "EUR", "Total 0,00"], None),
        (
            Merchant.PINGO_DOCE,
            ["ESTRADA DA LUZ", "Tel.: 210000000", "Pingo Doce - Distribuição Alimentar, S.A.", "Artigos"],
            "ESTRADA DA LUZ",
        ),
        (
            Merchant.CONTINENTE,
            ["BD Defensores Chaves", "Modelo Continente Hipermercados, SA", "IVA DESCRICAO VALOR"],
            "BD Defensores Chaves",
        ),
        (Merchant.CONTINENTE, ["Modelo Continente Hipermercados, SA", "IVA DESCRICAO VALOR"], None),
        (Merchant.AUCHAN, ["Loja Exemplo", "AUCHAN RETAIL PORTUGAL, S.A."], None),
    ],
)
def test_store_label_is_read_from_the_header(merchant: Merchant, lines: list[str], store: str | None) -> None:
    assert parser_for(merchant).parse(doc_from_lines(lines)).store_label == store


CONTINENTE_LINES = [
    "Modelo Continente Hipermercados, SA",
    "Nro:FS CNP203/131123 09/10/2026 15:16",
    "IVA DESCRICAO VALOR",
    "Higiene:",
    "(C) DEO SP NIVEA MEN BLACK&WHI 150",
    "2 X 2,99 5,98",
    "POUPANCA 4,00",
    "Charcutaria&Queijos:",
    "(A) QJ CABRA FATIAS CONTINENTE 150G 2,19",
    "SUBTOTAL 8,17",
    "Desconto Cartao Utilizado 5,00",
    "TOTAL A PAGAR 3,17",
    "Total de descontos e poupancas 4,00",
    "%IVA Total Liq. IVA Total",
    "(A) 6,00% 1,30 0,08 1,38",
    "26-10-09 15:16:45 Per051 Tr088 Msg138",
]


@pytest.mark.req("RF-06")
def test_continente_multiline_item_savings_and_card_discount() -> None:
    parsed = parser_for(Merchant.CONTINENTE).parse(doc_from_lines(CONTINENTE_LINES))
    deo, cheese = parsed.lines
    assert deo.raw_description == "DEO SP NIVEA MEN BLACK&WHI 150"
    # The printed price is already net of "POUPANCA": the saving is added back as discount.
    assert (deo.quantity, deo.unit, deo.unit_price_cents, deo.line_total_cents, deo.discount_cents) == (
        Decimal(2),
        "un",
        499,
        998,
        400,
    )
    assert (cheese.quantity, cheese.line_total_cents, cheese.discount_cents) == (Decimal(1), 219, 0)
    assert parsed.total_cents == 817  # SUBTOTAL, not "TOTAL A PAGAR" (depends on card balance used)
    assert parsed.total_discount_cents == 400
    assert parsed.subtotal_cents is None and parsed.tax_cents is None  # VAT table skewed by card discount
    assert "card_discount_ignored" in parsed.notes
    assert parsed.receipt_ref == "FS CNP203/131123"
    assert parsed.purchase_at == datetime(2026, 10, 9, 15, 16, tzinfo=LISBON)


@pytest.mark.req("RF-06")
def test_continente_quantity_line_without_description_is_kept_unresolved() -> None:
    parsed = parser_for(Merchant.CONTINENTE).parse(
        doc_from_lines(["IVA DESCRICAO VALOR", "3 X 2,69 8,07", "SUBTOTAL 8,07"])
    )
    assert len(parsed.lines) == 1 and parsed.lines[0].unresolved


AUCHAN_LINES = [
    "AUCHAN RETAIL PORTUGAL, S.A.",
    "FATURA",
    "N0.4111032026100001/001/10",
    "IVA ARTIGO/QT/LI PRECO VALOR",
    "Perfumaria",
    "I2 COLGATE MAX WHITE ON",
    "2 X 2,68 5,36",
    "I2 ELIXIR AUCHAN 500 ML 1,40",
    "3 volumes 6,76",
    "TOTAL PAGO 6,76",
    "Visa 0,00",
    "TROCO",
    "RESUMO DO IVA",
    "TAXA BASE VALOR",
    "23,00% (I2) 5,50 1,26",
    "TOTAL 5,50 1,26",
    "Data Emissao : 2026-10-18 13:27",
]


@pytest.mark.req("RF-06")
def test_auchan_items_total_vat_reference_and_date() -> None:
    parsed = parser_for(Merchant.AUCHAN).parse(doc_from_lines(AUCHAN_LINES))
    toothpaste, mouthwash = parsed.lines
    assert toothpaste.raw_description == "COLGATE MAX WHITE ON"
    assert (toothpaste.quantity, toothpaste.unit, toothpaste.unit_price_cents) == (Decimal(2), "un", 268)
    assert (toothpaste.line_total_cents, toothpaste.tax_code) == (536, "I2")
    assert (mouthwash.quantity, mouthwash.line_total_cents) == (Decimal(1), 140)
    assert (parsed.total_cents, parsed.subtotal_cents, parsed.tax_cents) == (676, 550, 126)
    assert parsed.receipt_ref == "4111032026100001/001/10"
    assert parsed.purchase_at == datetime(2026, 10, 18, 13, 27, tzinfo=LISBON)
    assert parsed.parser_version == "auchan-1.0"


@pytest.mark.req("RF-06")
def test_auchan_tolerates_ocr_damage_in_class_quantity_and_total() -> None:
    lines = [
        "IVA ARTIGO/QT/LI PRECO VALOR",
        "12 COLGATE MAX WHIIE ON",  # "I2" read as "12"
        "X 2,68 5,36",  # quantity digit lost
        "3 volunes 6,76",  # "TOTAL PAGO" line not read
    ]
    lines += ["IUA Liquidado 23,00% (12) 5,50 1,26"]  # heading glued to the VAT row
    parsed = parser_for(Merchant.AUCHAN).parse(doc_from_lines(lines))
    (item,) = parsed.lines
    assert (parsed.subtotal_cents, parsed.tax_cents) == (550, 126)
    assert (item.quantity, item.unit_price_cents, item.line_total_cents) == (Decimal(2), 268, 536)
    assert item.field_confidence["quantity"] < item.field_confidence["line_total"]  # rebuilt, less sure
    assert parsed.total_cents == 676


@pytest.mark.req("RF-06")
def test_auchan_lines_it_does_not_understand_are_kept_unresolved() -> None:
    lines = [
        "IVA ARTIGO/QT/LI PRECO VALOR",
        "I1 LEITE 0,89",
        "Desconto Cartao 0,20",  # discounts are not calibrated: never guessed
        "I1 DESCRICAO SEM PRECO",  # description whose quantity line never came
        "TOTAL PAGO 0,89",
    ]
    parsed = parser_for(Merchant.AUCHAN).parse(doc_from_lines(lines))
    assert [line.unresolved for line in parsed.lines] == [False, True, True]
    assert parsed.lines[1].line_total_cents == 20


@pytest.mark.req("RF-06")
def test_generic_parser_never_reaches_threshold() -> None:
    parsed = parser_for(Merchant.UNKNOWN).parse(
        doc_from_lines(["MERCEARIA", "LEITE 0,89", "TOTAL A PAGAR 0,89", "Data: 20/09/2026 11:05"])
    )
    assert parsed.lines and all(
        v <= CONFIDENCE_CAP for line in parsed.lines for v in line.field_confidence.values()
    )


@pytest.mark.req("RF-06")
def test_invalid_ocr_date_is_discarded_and_sources_are_cross_checked() -> None:
    # Real case: OCR read "2026-18-10" (month 18) next to two correct dates.
    doc = doc_from_lines(["Data de Venda: 2026-18-10", "10.10.26 13:45", "2026-10-10 13:46:11"])
    dt, conf = choose_purchase_datetime(parse_date_candidates(doc.lines))
    assert dt == datetime(2026, 10, 10, 13, 45, tzinfo=LISBON)
    assert conf >= 0.9


@pytest.mark.req("RF-06")
def test_conflicting_single_dates_get_low_confidence() -> None:
    doc = doc_from_lines(["2026-10-10 10:00", "2026-11-10 10:00"])
    _, conf = choose_purchase_datetime(parse_date_candidates(doc.lines))
    assert conf < 0.8


@pytest.mark.req("PT-7")
def test_purchase_time_is_lisbon_local_time_across_dst() -> None:
    summer = choose_purchase_datetime(parse_date_candidates(doc_from_lines(["2026-07-01 23:30"]).lines))[0]
    winter = choose_purchase_datetime(parse_date_candidates(doc_from_lines(["2026-12-31 23:30"]).lines))[0]
    assert summer is not None and summer.utcoffset().total_seconds() == 3600  # type: ignore[union-attr]
    assert winter is not None and winter.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


@pytest.mark.req("F-09")
@pytest.mark.parametrize(
    ("lines", "merchant"),
    [
        (["Pingo Doce - Distribuição Alimentar", "NIPC: 500829993"], Merchant.PINGO_DOCE),
        (["LIDL & Cia - LISBOA", "NIF:503340855"], Merchant.LIDL),
        (["CONTINENTE", "MODELO CONTINENTE HIPERMERCADOS"], Merchant.CONTINENTE),
        (["BD Defensores Chaves", "Registada CRC Porto sob nº 502011475"], Merchant.CONTINENTE),
        (["AUCHAN PORTUGAL", "auchan.pt"], Merchant.AUCHAN),
        (["Cap. Social: 100.000.000 Euros", "Cuntribuinte Nr 503003808"], Merchant.AUCHAN),
        (["MERCEARIA DO BAIRRO"], Merchant.UNKNOWN),
    ],
)
def test_classification(lines: list[str], merchant: Merchant) -> None:
    assert classify(doc_from_lines(lines)).merchant == merchant


@pytest.mark.req("RF-02")
def test_hint_used_when_document_has_no_signals() -> None:
    result = classify(doc_from_lines(["SEM CABECALHO"]), hint=Merchant.LIDL)
    assert result.merchant == Merchant.LIDL and result.confidence >= 0.8


@pytest.mark.req("RF-02")
def test_hint_conflict_is_flagged() -> None:
    result = classify(doc_from_lines(["Pingo Doce", "500829993"]), hint=Merchant.LIDL)
    assert result.merchant == Merchant.PINGO_DOCE and result.hint_conflict


@pytest.mark.req("RF-06")
def test_lidl_percent_discount_applies_to_the_previous_item() -> None:
    lines = [
        "LIDL & Cia",
        "EUR",
        "DORITOS TEX MEX 3,39 A",
        "DESCONTO 20% -0,68",
        "------------",
        "Total 2,71",
    ]
    parsed = parser_for(Merchant.LIDL).parse(doc_from_lines(lines))
    assert [(line.raw_description, line.discount_cents) for line in parsed.lines] == [("DORITOS TEX MEX", 68)]


@pytest.mark.req("RF-06")
def test_pingo_doce_card_balance_is_a_way_of_paying_not_a_discount() -> None:
    lines = [
        "Artigos",
        " E LINHA PD 500 G 1,79",
        " C CROISSANT C SEMENTES 2 X 0,89 1,78",
        "Poupança Imediata (0,40)",
        "Descontos Extra",
        "Saldo Pingo Doce (2,00)",
        "Resumo",
        "TOTAL 3,57",
        "TOTAL POUPANÇA (2,40)",
        "TOTAL A PAGAR 1,17",
        "Resumo IVA",
        "C 6% 1,04 0,06 1,10",
    ]
    parsed = parser_for(Merchant.PINGO_DOCE).parse(doc_from_lines(lines))
    assert [line.raw_description for line in parsed.lines] == ["LINHA PD 500 G", "CROISSANT C SEMENTES"]
    assert parsed.total_cents == 317  # what the purchase was worth: 3,57 less the 0,40 saving
    assert parsed.total_discount_cents == 40
    assert parsed.subtotal_cents is None and parsed.tax_cents is None  # the VAT table covers 1,17 only
    assert parsed.notes == []


ALDI_LOST_COMMA = [
    "DENOMINAÇÃO PREÇO IVA",
    "CENOURA 2KG 1,99 € 1",
    "BATATA BRANCA 2,5KG 229 E",
    "SORTIDO DE PÃO, 500 G",
    "ESPINAFRES, 100G 0,69 € 1",
]


@pytest.mark.req("RF-06")
@pytest.mark.parametrize(("total", "recovered"), [("4,97", True), ("5,00", False), ("2,68", False)])
def test_ocr_price_that_lost_its_comma_is_taken_only_when_it_closes_the_total(
    total: str, recovered: bool
) -> None:
    doc = doc_from_lines([*ALDI_LOST_COMMA, f"A PAGAR {total} €"], route=ProcessingRoute.LOCAL_OCR)
    parsed = parser_for(Merchant.ALDI).parse(doc)
    names = [line.raw_description for line in parsed.lines]
    assert ("BATATA BRANCA 2,5KG" in names) is recovered
    assert "SORTIDO DE PÃO," not in names  # "500 G" is never a price: it does not close any of these totals
    if recovered:
        potato = parsed.lines[names.index("BATATA BRANCA 2,5KG")]
        assert names.index("BATATA BRANCA 2,5KG") == 1  # in its place on the receipt
        assert potato.line_total_cents == 229
        assert potato.field_confidence["line_total"] < 0.8  # reviewed
