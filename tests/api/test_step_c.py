"""Step C (spec 010): groups, «above/below what you usually pay» (D11), chains compared per group (D12),
«Por completar» and the user's rules (D13). Synthetic receipts only, inserted straight into the database."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from basketiq.db.models import Product, Receipt, ReceiptLine, User, UserLineRule
from basketiq.domain.status import Merchant, ReceiptStatus

LISBON = ZoneInfo("Europe/Lisbon")
TODAY = date(2026, 11, 15)
MILK = "LEITE UHT MEIO GORDO 1L"
FETA = "QUEIJO FETA 200G"
LETTUCE = "SALADA ICEBERGUE"

# (description, quantity, unit, line total in cents, discount in cents)
Line = tuple[str, str, str, int, int]


@pytest.fixture(autouse=True)
def fixed_day() -> Iterator[None]:
    from basketiq.api.deps import get_today
    from basketiq.api.main import app

    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)


@pytest.fixture
def user(client: TestClient, login: Callable[..., User]) -> User:
    return login(client)


@pytest.fixture
def buy(db: Session) -> Callable[..., uuid.UUID]:
    """Insert a receipt straight into the database (no parsing involved)."""

    def _buy(
        owner: User,
        days_ago: int,
        lines: list[Line],
        *,
        merchant: str = Merchant.LIDL,
        status: ReceiptStatus = ReceiptStatus.CONFIRMED,
        product: Product | None = None,
    ) -> uuid.UUID:
        rid = uuid.uuid4()
        day = TODAY - timedelta(days=days_ago)
        db.add(
            Receipt(
                id=rid,
                user_id=owner.id,
                merchant=merchant,
                purchase_at=datetime.combine(day, datetime.min.time().replace(hour=18), LISBON),
                source_type="PDF",
                content_type="application/pdf",
                size_bytes=1,
                object_key=f"originals/{owner.id}/{rid}",
                processing_status=status,
            )
        )
        db.flush()
        for n, (description, qty, unit, total, discount) in enumerate(lines, start=1):
            db.add(
                ReceiptLine(
                    receipt_id=rid,
                    line_number=n,
                    raw_description=description,
                    normalized_product_id=product.id if product else None,
                    quantity_decimal=Decimal(qty),
                    unit=unit,
                    line_total_cents=total,
                    discount_cents=discount,
                )
            )
        db.commit()
        return rid

    return _buy


def milk(total: int, qty: str = "1") -> Line:
    return (MILK, qty, "un", total, 0)


def lines_of(client: TestClient, rid: uuid.UUID) -> list[dict[str, Any]]:
    resp = client.get(f"/v1/receipts/{rid}")
    assert resp.status_code == 200, resp.text
    return resp.json()["lines"]  # type: ignore[no-any-return]


def check_of(client: TestClient, rid: uuid.UUID, n: int = 0) -> dict[str, Any]:
    return lines_of(client, rid)[n]["grouping"]["price_check"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------- D7: only confirmed receipts


@pytest.mark.req("F-62")
def test_a_receipt_is_grouped_only_once_it_is_confirmed(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    pending = buy(user, 0, [milk(100), (FETA, "1", "un", 250, 0)], status=ReceiptStatus.REVIEW_REQUIRED)
    resp = client.get(f"/v1/receipts/{pending}").json()
    assert [line["grouping"] for line in resp["lines"]] == [None, None]
    assert resp["ungrouped_descriptions"] == 0

    confirmed = buy(user, 0, [milk(100), (FETA, "1", "un", 250, 0), ("DEPÓSITO", "1", "un", 10, 0)])
    resp = client.get(f"/v1/receipts/{confirmed}").json()
    grouping = [line["grouping"] for line in resp["lines"]]
    assert grouping[0]["status"] == "grouped"
    assert grouping[0]["group"] == {
        "slug": "leite-uht",
        "name": "Leite UHT",
        "class_slug": "laticinios-e-ovos",
    }
    assert grouping[0]["group_source"] == "catalog"
    assert grouping[0]["size"] == {"value": "1", "unit": "l", "source": "description"}
    assert grouping[1]["status"] == "ungrouped" and grouping[1]["group"] is None
    assert grouping[2]["status"] == "not_comparable"  # a deposit is never «por agrupar»
    assert resp["ungrouped_descriptions"] == 1


# ---------------------------------------------------------------- D11


@pytest.mark.req("F-63")
def test_a_line_above_the_users_average_says_so_with_the_numbers(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    for days in (40, 30, 20):
        buy(user, days, [milk(100)])
    rid = buy(user, 0, [milk(130)])
    assert check_of(client, rid) == {
        "status": "above",
        "basis": "l",
        "purchases": 3,
        "needed": 3,
        "window_days": 90,
        "price_cents": 130,
        "average_cents": 100,
        "effect_cents": 30,
    }
    below = buy(user, 0, [milk(170, qty="2")], merchant=Merchant.ALDI)  # 0,85 €/l, another chain
    assert check_of(client, below)["status"] == "below"
    assert check_of(client, below)["effect_cents"] == -30


@pytest.mark.req("F-63")
def test_the_average_uses_the_net_price_and_the_size_and_excludes_the_compared_purchase(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    buy(user, 50, [("LEITE UHT MEIO GORDO 6X1L", "1", "un", 660, 60)])  # 6 l at 1,00 €/l after the discount
    buy(user, 40, [milk(100)])
    buy(user, 30, [("Leite UHT meio gordo 1 l", "2", "un", 200, 0)])
    buy(user, 95, [milk(500)])  # outside the 90-day window
    rid = buy(user, 0, [milk(100)])
    check = check_of(client, rid)
    assert (check["status"], check["purchases"], check["average_cents"]) == ("normal", 3, 100)


@pytest.mark.req("F-64")
def test_with_fewer_than_three_earlier_purchases_there_is_no_average_yet(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    buy(user, 30, [milk(100)])
    buy(user, 20, [milk(100)])
    rid = buy(user, 0, [milk(200)])
    check = check_of(client, rid)
    assert (check["status"], check["purchases"], check["needed"]) == ("no_average", 2, 3)
    assert "average_cents" not in check or check["average_cents"] is None


@pytest.mark.req("F-65")
@pytest.mark.parametrize(
    ("line", "status"),
    [
        (milk(110), "normal"),  # 10 % above: under 15 %
        (milk(116), "normal"),  # 16 % above but 0,16 € on the line: under 0,20 €
        (milk(232, qty="2"), "above"),  # 16 % above and 0,32 € on the line
    ],
)
def test_a_difference_needs_fifteen_percent_and_twenty_cents_on_the_line(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID], line: Line, status: str
) -> None:
    for days in (40, 30, 20):
        buy(user, days, [milk(100)])
    assert check_of(client, buy(user, 0, [line]))["status"] == status


@pytest.mark.req("F-75")
def test_a_weighed_line_is_priced_per_kg_from_its_net_value(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    for days in (40, 30, 20):
        buy(user, days, [("BANANA", "1.000", "kg", 100, 0)])
    rid = buy(user, 0, [("BANANA", "0.500", "kg", 80, 10)])  # 0,70 € net for 0,5 kg
    check = check_of(client, rid)
    assert (check["basis"], check["price_cents"], check["status"]) == ("kg", 140, "above")
    # The weight printed on the receipt is the size (origin «talão» in the CSV).
    assert lines_of(client, rid)[0]["grouping"]["size"] == {"value": "0.5", "unit": "kg", "source": "receipt"}


@pytest.mark.req("F-75")
def test_a_line_without_a_size_is_grouped_but_not_compared(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    rid = buy(user, 0, [(LETTUCE, "1", "un", 99, 0)])
    grouping = lines_of(client, rid)[0]["grouping"]
    assert grouping["group"]["slug"] == "alface-e-saladas"
    assert grouping["price_check"]["status"] == "missing_size"


# ---------------------------------------------------------------- D12


def comparison(client: TestClient, slug: str = "leite-uht") -> dict[str, Any]:
    resp = client.get(f"/v1/groups/{slug}/comparison")
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.req("F-66")
def test_two_chains_with_three_purchases_each_are_compared_without_ranking(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    for days in (30, 20, 10):
        buy(user, days, [milk(100)], merchant=Merchant.LIDL)
        buy(user, days, [milk(130)], merchant=Merchant.ALDI)
    result = comparison(client)
    assert result["status"] == "clear_difference"
    assert result["typical_difference_cents"] == 30
    assert (result["basis"], result["purchases"], result["average_cents"], result["window_days"]) == (
        "l",
        6,
        115,
        90,
    )
    assert [(c["merchant_name"], c["purchases"], c["average_cents"]) for c in result["chains"]] == [
        ("Aldi", 3, 130),
        ("Lidl", 3, 100),
    ]  # by name, never by price


@pytest.mark.req("F-67")
def test_without_two_chains_of_three_purchases_there_is_no_conclusion(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    assert comparison(client)["status"] == "no_purchases"
    for days in (30, 20, 10):
        buy(user, days, [milk(100)], merchant=Merchant.LIDL)
    assert comparison(client)["status"] == "single_chain"
    buy(user, 5, [milk(130)], merchant=Merchant.ALDI)
    buy(user, 4, [milk(130)], merchant=Merchant.ALDI)
    result = comparison(client)
    assert result["status"] == "not_enough_purchases"
    assert {c["merchant_name"]: c["missing_purchases"] for c in result["chains"]} == {"Aldi": 1, "Lidl": 0}
    assert result["typical_difference_cents"] is None


@pytest.mark.req("F-67")
def test_a_small_difference_between_chains_is_not_clear(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    for days in (30, 20, 10):
        buy(user, days, [milk(100)], merchant=Merchant.LIDL)
        buy(user, days, [milk(110)], merchant=Merchant.ALDI)
    result = comparison(client)
    assert result["status"] == "no_clear_difference"
    assert result["typical_difference_cents"] is None
    assert client.get("/v1/groups/nao-existe/comparison").status_code == 404
    assert client.get("/v1/groups/laticinios-e-ovos/comparison").status_code == 404  # a class is not a group


# ---------------------------------------------------------------- D13: «Por completar» and rules


def to_complete(client: TestClient, tab: str = "ungrouped") -> dict[str, Any]:
    resp = client.get("/v1/to-complete", params={"tab": tab})
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.req("F-68")
def test_to_complete_lists_the_descriptions_with_most_spending_first(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID], config_is: Any
) -> None:
    buy(user, 10, [(FETA, "1", "un", 250, 0), ("PARMIGIANO REGGIANO DOP", "1", "un", 400, 0), milk(100)])
    buy(user, 5, [(FETA, "1", "un", 250, 0), (LETTUCE, "1", "un", 99, 0)])
    buy(user, 1, [("MOLHO PESTO", "1", "un", 199, 0)], status=ReceiptStatus.REVIEW_REQUIRED)  # not confirmed
    result = to_complete(client)
    assert result["total"] == 2
    assert [(i["description"], i["purchases"], i["spent_cents"]) for i in result["items"]] == [
        (FETA, 2, 500),
        ("PARMIGIANO REGGIANO DOP", 1, 400),
    ]
    feta = result["items"][0]
    assert feta["hint"] == {"slug": "queijos", "name": "Queijos"}
    assert "queijo-fresco" in [s["slug"] for s in feta["suggestions"]]
    assert [i["description"] for i in to_complete(client, "missing_size")["items"]] == [LETTUCE]

    config_is(TO_COMPLETE_PAGE_SIZE=1)
    result = to_complete(client)
    assert (result["total"], len(result["items"]), result["page_size"]) == (2, 1, 1)


@pytest.mark.req("F-69")
def test_a_group_chosen_by_the_user_applies_to_past_purchases_and_can_be_undone(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    first = buy(user, 10, [(FETA, "1", "un", 250, 0)])
    buy(user, 5, [("Queijo  feta, 200G", "1", "un", 250, 0)])  # same normalised description
    resp = client.put(
        "/v1/line-rules",
        json={"targets": [{"chain": "LIDL", "description": FETA}], "group": "queijo-fresco"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_lines"] == 2
    rule = body["rules"][0]
    assert rule["group"]["slug"] == "queijo-fresco" and rule["not_comparable"] is False
    grouping = lines_of(client, first)[0]["grouping"]
    assert (grouping["group"]["slug"], grouping["group_source"]) == ("queijo-fresco", "user")
    assert to_complete(client)["total"] == 0
    assert [r["id"] for r in client.get("/v1/line-rules").json()] == [rule["id"]]

    assert client.delete(f"/v1/line-rules/{rule['id']}").status_code == 204  # «Desfazer»
    assert lines_of(client, first)[0]["grouping"]["status"] == "ungrouped"
    assert to_complete(client)["total"] == 1


@pytest.mark.req("F-70")
def test_a_size_given_once_by_the_user_makes_the_line_comparable(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    for days in (40, 30, 20):
        buy(user, days, [("ALFACE FRISADA 300G", "1", "un", 90, 0)])
    rid = buy(user, 0, [(LETTUCE, "1", "un", 99, 0)])
    resp = client.put(
        "/v1/line-rules",
        json={"targets": [{"chain": "LIDL", "description": LETTUCE}], "size": {"value": "0.3", "unit": "kg"}},
    )
    assert resp.status_code == 200, resp.text
    grouping = lines_of(client, rid)[0]["grouping"]
    assert grouping["size"] == {"value": "0.3", "unit": "kg", "source": "user"}
    assert (grouping["price_check"]["status"], grouping["price_check"]["price_cents"]) == ("normal", 330)
    assert to_complete(client, "missing_size")["total"] == 0

    # Clearing the only thing in the rule removes it.
    resp = client.put(
        "/v1/line-rules", json={"targets": [{"chain": "LIDL", "description": LETTUCE}], "size": None}
    )
    assert resp.json()["rules"] == []
    assert client.get("/v1/line-rules").json() == []


@pytest.mark.req("F-71")
def test_not_comparable_takes_the_description_out_of_the_list_until_undone(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID]
) -> None:
    rid = buy(user, 0, [(FETA, "1", "un", 250, 0)])
    target = {"targets": [{"chain": "LIDL", "description": FETA}]}
    assert client.put("/v1/line-rules", json={**target, "not_comparable": True}).status_code == 200
    assert lines_of(client, rid)[0]["grouping"]["status"] == "not_comparable"
    assert to_complete(client)["total"] == 0
    client.put("/v1/line-rules", json={**target, "not_comparable": False})
    assert to_complete(client)["total"] == 1


@pytest.mark.req("F-71")
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"group": "nao-existe"}, "unknown_group"),
        ({"group": "laticinios-e-ovos"}, "unknown_group"),  # a class, not a group
        ({"size": {"value": "500", "unit": "kg"}}, "implausible_size"),
        ({}, "nothing_to_change"),
    ],
)
def test_a_rule_the_catalogue_cannot_accept_is_refused(
    client: TestClient, user: User, change: dict[str, Any], code: str
) -> None:
    resp = client.put("/v1/line-rules", json={"targets": [{"description": FETA}], **change})
    assert resp.status_code == 422
    assert resp.json()["code"] == code


@pytest.mark.req("F-72")
def test_rules_and_comparisons_are_private_to_each_user(
    client: TestClient, login: Callable[..., User], user: User, buy: Callable[..., uuid.UUID]
) -> None:
    mine = buy(user, 0, [(FETA, "1", "un", 250, 0)])
    other_client = TestClient(client.app)
    other = login(other_client)
    buy(other, 0, [(FETA, "1", "un", 250, 0)])
    for days in (30, 20, 10):
        buy(other, days, [milk(100)])
    resp = other_client.put(
        "/v1/line-rules", json={"targets": [{"chain": "LIDL", "description": FETA}], "group": "queijo-fresco"}
    )
    assert resp.json()["applied_lines"] == 1  # only the other user's line
    rule_id = resp.json()["rules"][0]["id"]

    assert lines_of(client, mine)[0]["grouping"]["status"] == "ungrouped"
    assert client.get("/v1/line-rules").json() == []
    assert client.delete(f"/v1/line-rules/{rule_id}").status_code == 404
    assert comparison(client)["status"] == "no_purchases"


@pytest.mark.req("F-73")
@pytest.mark.req("F-23")
def test_deleting_the_account_deletes_the_users_rules(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID], db: Session
) -> None:
    buy(user, 0, [(FETA, "1", "un", 250, 0)])
    client.put(
        "/v1/line-rules", json={"targets": [{"chain": "LIDL", "description": FETA}], "not_comparable": True}
    )
    assert db.scalar(select(func.count()).select_from(UserLineRule)) == 1
    assert client.delete("/v1/me").status_code == 200
    assert db.scalar(select(func.count()).select_from(UserLineRule)) == 0


# ---------------------------------------------------------------- catalogue and product


@pytest.mark.req("F-74")
def test_the_catalogue_lists_the_fifteen_classes_and_their_groups(client: TestClient, user: User) -> None:
    resp = client.get("/v1/catalog/groups")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] >= 1
    assert len(body["classes"]) == 15
    dairy = next(c for c in body["classes"] if c["slug"] == "laticinios-e-ovos")
    assert {"slug": "leite-uht", "name": "Leite UHT", "bases": ["l"], "comparable": True} in dairy["groups"]
    assert TestClient(client.app).get("/v1/catalog/groups").status_code == 401


@pytest.mark.req("F-74")
def test_the_product_screen_gets_the_group_of_its_latest_purchase(
    client: TestClient, user: User, buy: Callable[..., uuid.UUID], db: Session
) -> None:
    product = Product(canonical_name=MILK, normalized_key=MILK)
    db.add(product)
    db.commit()
    buy(user, 0, [milk(100)], product=product)
    resp = client.get(f"/v1/products/{product.id}/analytics")
    assert resp.status_code == 200, resp.text
    assert resp.json()["group"]["slug"] == "leite-uht"
