from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.flows import import_receipt
from tests.helpers import make_pdf


def pingo(date: str, time: str, items: list[str], total: str) -> bytes:
    d, m, y = date.split("/")
    return make_pdf(
        [
            "Pingo Doce - Distribuição Alimentar, S.A.",
            "NIPC: 500829993",
            "Artigos",
            *items,
            "Resumo",
            f"TOTAL A PAGAR {total}",
            f"000001 {y}-{m}-{d} {time} 0001",
            f"Data de emissão:{date}",
        ]
    )


def lidl(date_iso: str, time: str, items: list[str], total: str) -> bytes:
    y, m, d = date_iso.split("-")
    return make_pdf(
        [
            "LIDL & Cia",
            "NIF:503340855",
            f"Data de Venda: {date_iso}",
            "EUR",
            *items,
            f"Total {total}",
            f"{d}.{m}.{y[2:]} {time}",
        ]
    )


@pytest.fixture
def history(client: TestClient, login: Any, storage: Any, run_jobs: Any) -> Any:
    login(client)
    docs = [
        pingo("05/08/2026", "10:00", [" C LEITE MEIO GORDO 0,79", " C BANANA 1,000 X 1,50 1,50"], "2,29"),
        pingo(
            "02/09/2026",
            "18:00",
            [" C LEITE MEIO GORDO 2 X 0,85 1,70", " C BANANA 0,500 X 1,60 0,80"],
            "2,50",
        ),
        lidl("2026-09-20", "09:30", ["LEITE MEIO GORDO 0,75 B", "PAO 0,40 B"], "1,15"),
        # 30 September 23:30 in Lisbon is still September even though it is 22:30 UTC.
        pingo("30/09/2026", "23:30", [" C LEITE MEIO GORDO 0,90"], "0,90"),
        # 1 October 00:15 Lisbon (= 30 Sep 23:15 UTC) belongs to October.
        pingo("01/10/2026", "00:15", [" C AGUA 0,30"], "0,30"),
    ]
    for data in docs:
        receipt = import_receipt(client, storage, run_jobs, data)
        assert receipt["status"] == "CONFIRMED", receipt["review_reasons"]
    return client


@pytest.mark.req("F-18")
@pytest.mark.req("PT-AC-4")
def test_month_filter_and_lisbon_timezone(history: TestClient) -> None:
    s = history.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()
    assert s["cards"]["purchases"] == 3
    assert s["cards"]["total_spent_cents"] == 250 + 115 + 90
    october = history.get("/v1/analytics/summary?from=2026-10-01&to=2026-10-31").json()
    assert october["cards"]["purchases"] == 1 and october["cards"]["total_spent_cents"] == 30


@pytest.mark.req("RF-09")
def test_cards_variation_series_and_rankings(history: TestClient) -> None:
    s = history.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()
    cards = s["cards"]
    # Previous equivalent period: 2 Aug – 31 Aug → one purchase of 2,29.
    assert cards["previous_period"]["total_spent_cents"] == 229
    assert cards["variation_pct"]["total_spent"] == round((455 - 229) / 229 * 100, 1)
    # Units: 2 + 1 + 1 milk, 1 bread, banana (weighed) counts as one article.
    assert cards["units"] == "6"
    assert s["series"]["granularity"] == "day"
    assert len(s["series"]["points"]) == 30
    assert sum(p["spent_cents"] for p in s["series"]["points"]) == 455
    assert {m["merchant"]: m["spent_cents"] for m in s["by_merchant"]} == {"PINGO_DOCE": 340, "LIDL": 115}
    assert s["top_products_by_value"][0]["name"] == "LEITE MEIO GORDO"


@pytest.mark.req("F-19")
@pytest.mark.req("PT-AC-5")
def test_merchant_filter_changes_every_card_and_chart(history: TestClient) -> None:
    s = history.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30&merchant=LIDL").json()
    assert s["cards"]["purchases"] == 1 and s["cards"]["total_spent_cents"] == 115
    assert [m["merchant"] for m in s["by_merchant"]] == ["LIDL"]
    assert sum(p["spent_cents"] for p in s["series"]["points"]) == 115
    assert {p["name"] for p in s["top_products_by_value"]} == {"LEITE MEIO GORDO", "PAO"}


@pytest.mark.req("F-20")
@pytest.mark.req("RF-10")
def test_product_history_weighted_average_and_merchant_comparison(history: TestClient) -> None:
    milk = next(p for p in history.get("/v1/products?q=leite").json() if p["name"] == "LEITE MEIO GORDO")
    a = history.get(f"/v1/products/{milk['id']}/analytics").json()
    assert a["purchases"] == 4
    assert a["price_basis"] == "un"
    assert a["total_quantity"] == "5"
    # Weighted: (79 + 170 + 75 + 90) / 5 units = 82.8 → 83 cents.
    assert a["avg_price_cents"] == 83
    assert (a["min_price_cents"], a["max_price_cents"]) == (75, 90)
    by_merchant = {m["merchant"]: m for m in a["by_merchant"]}
    assert by_merchant["LIDL"]["avg_price_cents"] == 75
    assert [h["date"] for h in a["history"]] == ["2026-08-05", "2026-09-02", "2026-09-20", "2026-09-30"]


@pytest.mark.req("RF-10")
def test_weighed_product_uses_price_per_kg(history: TestClient) -> None:
    banana = history.get("/v1/products?q=banana").json()[0]
    a = history.get(f"/v1/products/{banana['id']}/analytics").json()
    assert a["price_basis"] == "kg"
    assert a["total_quantity"] == "1.5"
    assert a["avg_price_cents"] == 153  # (150 + 80) / 1.5 kg
    assert (a["min_price_cents"], a["max_price_cents"]) == (150, 160)


@pytest.mark.req("F-21")
def test_empty_period_and_insufficient_data(history: TestClient) -> None:
    empty = history.get("/v1/analytics/summary?from=2025-01-01&to=2025-01-31").json()
    assert empty["empty"] and empty["cards"]["purchases"] == 0
    assert empty["series"]["insufficient_data"]
    one_day = history.get("/v1/analytics/summary?from=2026-09-20&to=2026-09-20").json()
    assert one_day["series"]["insufficient_data"]


@pytest.mark.req("RF-09")
def test_year_view_uses_monthly_buckets(history: TestClient) -> None:
    s = history.get("/v1/analytics/summary?from=2026-01-01&to=2026-12-31").json()
    assert s["series"]["granularity"] == "month"
    points = {p["period"]: p["spent_cents"] for p in s["series"]["points"]}
    assert points["2026-08"] == 229 and points["2026-09"] == 455 and points["2026-10"] == 30


@pytest.mark.req("RF-11")
def test_invalid_ranges(history: TestClient) -> None:
    assert history.get("/v1/analytics/summary?from=2026-09-30&to=2026-09-01").status_code == 422
    assert history.get("/v1/analytics/summary?from=2020-01-01&to=2026-09-01").status_code == 422
