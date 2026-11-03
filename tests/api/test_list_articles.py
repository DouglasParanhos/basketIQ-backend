"""Smart list, first increment (spec 015 US-A): GET /v1/lists/articles. Synthetic data only."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from basketiq.db.models import Product, Receipt, ReceiptLine, User
from basketiq.domain.status import MERCHANT_NAMES, Merchant, ReceiptStatus

LISBON = ZoneInfo("Europe/Lisbon")
ARTICLES = "/v1/lists/articles"
TODAY = date(2026, 11, 15)
FIRST_DAY = TODAY - timedelta(days=89)  # 90 days, today included

# (product, quantity, unit, line total, discount); a product of None is a line nobody identified.
Line = tuple[Product | None, str, str, int, int]


@pytest.fixture(autouse=True)
def setup(config_is: Any) -> Iterator[None]:
    """Fix the day and turn the smart list on (the repository file keeps it off)."""
    from basketiq.api.deps import get_today
    from basketiq.api.main import app

    config_is(smart_list=True)
    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)


@pytest.fixture
def user(client: TestClient, login: Callable[..., User]) -> User:
    return login(client)


@pytest.fixture
def product(db: Session) -> Callable[[str], Product]:
    def _product(name: str) -> Product:
        p = Product(canonical_name=name, normalized_key=name.upper())
        db.add(p)
        db.commit()
        return p

    return _product


@pytest.fixture
def buy(db: Session) -> Callable[..., None]:
    """Insert a receipt straight into the database (no parsing involved)."""

    def _buy(
        owner: User,
        day: date,
        lines: list[Line],
        *,
        merchant: str = Merchant.LIDL,
        merchant_name: str | None = None,
        status: ReceiptStatus = ReceiptStatus.CONFIRMED,
    ) -> None:
        rid = uuid.uuid4()
        db.add(
            Receipt(
                id=rid,
                user_id=owner.id,
                merchant=merchant,
                merchant_name=merchant_name,
                purchase_at=datetime.combine(day, datetime.min.time().replace(hour=18), LISBON),
                source_type="image",
                content_type="image/png",
                size_bytes=1,
                object_key=f"originals/{owner.id}/{rid}",
                processing_status=status,
            )
        )
        db.flush()
        for n, (prod, qty, unit, total, discount) in enumerate(lines, start=1):
            db.add(
                ReceiptLine(
                    receipt_id=rid,
                    line_number=n,
                    raw_description=f"ARTIGO {n}",
                    normalized_product_id=prod.id if prod else None,
                    quantity_decimal=Decimal(qty),
                    unit=unit,
                    line_total_cents=total,
                    discount_cents=discount,
                )
            )
        db.commit()

    return _buy


def articles(client: TestClient) -> dict[str, Any]:
    resp = client.get(ARTICLES)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.req("F-152")
def test_the_list_does_not_exist_while_the_option_is_off(
    client: TestClient, login: Callable[..., User], config_is: Any
) -> None:
    config_is(smart_list=False)
    assert client.get(ARTICLES).status_code == 404  # before the session check
    login(client, consent=False)
    assert client.get(ARTICLES).status_code == 404  # and before the consent check
    login(client)
    resp = client.get(ARTICLES)
    assert resp.status_code == 404 and resp.json()["code"] == "not_found"


@pytest.mark.req("F-152")
def test_the_list_needs_a_session_and_the_privacy_policy(
    client: TestClient, login: Callable[..., User]
) -> None:
    assert client.get(ARTICLES).status_code == 401
    login(client, consent=False)
    resp = client.get(ARTICLES)
    assert resp.status_code == 403 and resp.json()["code"] == "privacy_consent_required"


@pytest.mark.req("F-147")
def test_only_the_users_confirmed_purchases_in_the_window_count(
    client: TestClient,
    user: User,
    login: Callable[..., User],
    product: Callable[[str], Product],
    buy: Callable[..., None],
) -> None:
    milk = product("Leite")
    buy(user, FIRST_DAY, [(milk, "1", "un", 99, 0)])  # first day of the window: in
    buy(user, TODAY, [(milk, "1", "un", 99, 0)])  # today: in
    buy(user, FIRST_DAY - timedelta(days=1), [(milk, "1", "un", 99, 0)])  # one day too old
    buy(user, TODAY, [(milk, "1", "un", 99, 0)], status=ReceiptStatus.REVIEW_REQUIRED)
    buy(login(TestClient(client.app)), TODAY, [(milk, "1", "un", 99, 0)])  # someone else's

    body = articles(client)
    assert (body["window_days"], body["date_from"], body["date_to"]) == (90, "2026-08-18", "2026-11-15")
    [item] = body["items"]
    assert (item["name"], item["purchases"], item["spent_cents"]) == ("Leite", 2, 198)


@pytest.mark.req("F-148")
@pytest.mark.req("F-149")
@pytest.mark.req("F-150")
def test_each_store_has_its_own_prices_with_the_discount_marked(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    yogurt = product("Iogurte grego")
    buy(user, date(2026, 8, 20), [(yogurt, "1", "un", 149, 0)])
    buy(user, date(2026, 9, 10), [(yogurt, "1", "un", 149, 24)])  # paid 1,25 with a discount
    buy(user, date(2026, 10, 20), [(yogurt, "2", "un", 322, 0)])  # 1,61 each
    buy(user, date(2026, 11, 1), [(yogurt, "1", "un", 199, 0)], merchant=Merchant.PINGO_DOCE)

    [item] = articles(client)["items"]
    assert item["purchases"] == 4
    assert item["packages"] == "5"
    assert item["packages_per_month"] == 1.7  # 5 packages in 90 days
    assert item["avg_interval_days"] == 24.3  # 20 Aug → 1 Nov: 73 days over 3 gaps
    assert (item["spent_cents"], item["avg_price_cents"]) == (795, 159)  # 7,95 € for 5
    assert (item["price_basis"], item["mixed_units"], item["last_purchase"]) == ("un", False, "2026-11-01")

    lidl, pingo = item["articles"]  # most purchases first
    assert (lidl["merchant"], lidl["merchant_name"], lidl["purchases"]) == (
        "LIDL",
        MERCHANT_NAMES[Merchant.LIDL],
        3,
    )
    assert lidl["avg_price_cents"] == 149  # 5,96 € for 4 packages
    assert lidl["lowest"] == {"price_cents": 125, "date": "2026-09-10", "discounted": True}
    assert lidl["highest"] == {"price_cents": 161, "date": "2026-10-20", "discounted": False}
    assert lidl["last"] == lidl["highest"]
    assert (pingo["merchant"], pingo["purchases"], pingo["avg_price_cents"]) == ("PINGO_DOCE", 1, 199)


@pytest.mark.req("F-149")
def test_lines_in_another_unit_count_as_purchases_but_not_in_the_prices(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    chicken = product("Peito de frango")
    buy(user, date(2026, 9, 1), [(chicken, "0.500", "kg", 300, 0)])  # 6,00 €/kg
    buy(user, date(2026, 9, 15), [(chicken, "1.000", "kg", 550, 0)])  # 5,50 €/kg
    buy(user, date(2026, 10, 1), [(chicken, "1", "un", 250, 0)])  # a pack: no weight

    [item] = articles(client)["items"]
    assert (item["price_basis"], item["mixed_units"], item["packages"]) == ("kg", True, "3")
    assert item["avg_price_cents"] == 567  # 8,50 € for 1,5 kg
    [store] = item["articles"]
    assert store["purchases"] == 3
    assert (store["lowest"]["price_cents"], store["highest"]["price_cents"]) == (550, 600)
    assert store["last"]["date"] == "2026-09-15"


@pytest.mark.req("F-150")
@pytest.mark.req("F-151")
def test_items_come_by_spending_and_unidentified_lines_are_only_counted(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    rice, coffee = product("Arroz"), product("Café")
    buy(
        user,
        date(2026, 10, 5),
        [(rice, "1", "un", 129, 0), (coffee, "1", "un", 450, 0), (None, "1", "un", 80, 0)],
    )

    body = articles(client)
    assert [i["name"] for i in body["items"]] == ["Café", "Arroz"]
    assert body["excluded_lines"] == 1


@pytest.mark.req("F-147")
def test_other_stores_are_told_apart_by_their_name(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    bread = product("Pão")
    for name in ("Padaria Sol", "Padaria Lua", None):
        buy(
            user,
            date(2026, 10, 5),
            [(bread, "1", "un", 120, 0)],
            merchant=Merchant.UNKNOWN,
            merchant_name=name,
        )

    [item] = articles(client)["items"]
    assert sorted((a["merchant"], a["merchant_name"]) for a in item["articles"]) == [
        (None, "Outros"),
        (None, "Padaria Lua"),
        (None, "Padaria Sol"),
    ]
