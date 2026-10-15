from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from basketiq.domain.status import Merchant
from basketiq.pipeline.classify import classify
from basketiq.pipeline.parsers import parser_for
from basketiq.pipeline.parsers.base import choose_purchase_datetime, parse_date_candidates
from basketiq.pipeline.parsers.generic_pt import CONFIDENCE_CAP
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
def test_generic_parser_never_reaches_threshold() -> None:
    parsed = parser_for(Merchant.CONTINENTE).parse(
        doc_from_lines(["CONTINENTE", "LEITE 0,89", "TOTAL A PAGAR 0,89", "Data: 20/09/2026 11:05"])
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
        (["AUCHAN PORTUGAL", "auchan.pt"], Merchant.AUCHAN),
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
