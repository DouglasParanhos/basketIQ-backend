"""Shopping list (RF-16, spec 019): items, last price paid and estimates. Synthetic data only."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from basketiq.db.models import Product, Receipt, ReceiptLine, ShoppingList, ShoppingListItem, User
from basketiq.domain.normalization import normalize_key
from basketiq.domain.status import Merchant, ReceiptStatus

LISBON = ZoneInfo("Europe/Lisbon")
TODAY = date(2026, 11, 15)
CURRENT = "/v1/lists/current"
ITEMS = f"{CURRENT}/items"
SUPERMARKETS = ["CONTINENTE", "PINGO_DOCE", "LIDL", "AUCHAN", "ALDI", "MERCADONA", "EL_CORTE_INGLES"]


@pytest.fixture(autouse=True)
def setup(config_is: Any) -> Iterator[None]:
    """Fix the day and turn the list on (the repository file keeps it off)."""
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
        p = Product(canonical_name=name, normalized_key=normalize_key(name))
        db.add(p)
        db.commit()
        return p

    return _product


@pytest.fixture
def buy(db: Session) -> Callable[..., None]:
    """A receipt with one line, straight into the database."""

    def _buy(
        owner: User,
        prod: Product,
        day: date,
        total: int,
        *,
        merchant: str = Merchant.LIDL,
        qty: str = "1",
        unit: str = "un",
        discount: int = 0,
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
        db.add(
            ReceiptLine(
                receipt_id=rid,
                line_number=1,
                raw_description="ARTIGO",
                normalized_product_id=prod.id,
                quantity_decimal=Decimal(qty),
                unit=unit,
                line_total_cents=total,
                discount_cents=discount,
            )
        )
        db.commit()

    return _buy


def the_list(client: TestClient) -> dict[str, Any]:
    resp = client.get(CURRENT)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


def add(client: TestClient, name: str, **fields: Any) -> dict[str, Any]:
    resp = client.post(ITEMS, json={"name": name, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def item_named(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(i for i in body["items"] if i["name"] == name)


def estimate(body: dict[str, Any], merchant: str) -> dict[str, Any]:
    return next(e for e in body["estimates"] if e["merchant"] == merchant)


@pytest.mark.req("F-300")
def test_the_list_does_not_exist_while_the_option_is_off(
    client: TestClient, user: User, config_is: Any
) -> None:
    config_is(smart_list=False)
    assert client.get(CURRENT).status_code == 404
    assert client.post(ITEMS, json={"name": "Leite"}).status_code == 404
    assert client.get(f"{CURRENT}/suggestions").status_code == 404


@pytest.mark.req("F-300")
def test_the_list_needs_a_session_and_the_csrf_token(client: TestClient, user: User) -> None:
    assert client.post(ITEMS, json={"name": "Leite"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    with TestClient(client.app, base_url="http://testserver") as anonymous:
        assert anonymous.get(CURRENT).status_code == 401


@pytest.mark.req("F-301")
def test_a_new_list_is_empty_with_the_categories_and_an_estimate_for_every_supermarket(
    client: TestClient, user: User
) -> None:
    body = the_list(client)
    assert body["items"] == []
    assert body["items_total"] == 0
    assert [c["name"] for c in body["categories"]][-1] == "Outros"
    assert [e["merchant"] for e in body["estimates"]] == SUPERMARKETS  # a fixed order: no ranking
    assert all(e["total_cents"] == 0 for e in body["estimates"])


@pytest.mark.req("F-301")
def test_the_supermarket_defaults_to_the_first_usual_one_and_can_be_chosen(
    client: TestClient, user: User
) -> None:
    assert client.patch("/v1/me/profile", json={"merchants": ["ALDI", "LIDL"]}).status_code == 200
    assert the_list(client)["merchant"] == "ALDI"
    resp = client.put(f"{CURRENT}/merchant", json={"merchant": "MERCADONA"})
    assert resp.status_code == 200
    assert resp.json()["merchant"] == "MERCADONA"
    assert the_list(client)["merchant"] == "MERCADONA"
    assert client.put(f"{CURRENT}/merchant", json={"merchant": "UNKNOWN"}).status_code == 422


@pytest.mark.req("F-302")
def test_an_item_gets_its_category_from_the_name_and_the_list_is_grouped_by_category(
    client: TestClient, user: User
) -> None:
    add(client, "Arroz agulha")
    add(client, "Qualquer coisa estranha")
    body = add(client, "Leite meio-gordo", quantity="6", unit="un")
    assert item_named(body, "Leite meio-gordo")["category_name"] == "Laticínios e ovos"
    assert item_named(body, "Leite meio-gordo")["quantity"] == "6"
    assert item_named(body, "Arroz agulha")["category_name"] == "Mercearia"
    assert item_named(body, "Qualquer coisa estranha")["category_name"] == "Outros"
    # Alphabetical by category, «Outros» last, whatever the order the items arrived in.
    assert [i["category_name"] for i in body["items"]] == ["Laticínios e ovos", "Mercearia", "Outros"]


@pytest.mark.req("F-302")
def test_a_name_matching_a_bought_product_is_linked_to_it(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    milk = product("Leite meio-gordo")
    buy(user, milk, TODAY - timedelta(days=5), 82)
    body = add(client, "leite  MEIO gordo")
    assert body["items"][0]["product_id"] == str(milk.id)
    assert body["items"][0]["expected"]["price_cents"] == 82


@pytest.mark.req("F-302")
def test_a_product_the_person_never_bought_cannot_be_linked(
    client: TestClient, user: User, product: Callable[[str], Product]
) -> None:
    other = product("Produto de outra pessoa")
    assert client.post(ITEMS, json={"name": "X", "product_id": str(other.id)}).status_code == 404


@pytest.mark.req("F-303")
def test_an_item_can_be_edited_including_its_category(client: TestClient, user: User) -> None:
    item_id = add(client, "Leite")["items"][0]["id"]
    resp = client.patch(
        f"{ITEMS}/{item_id}",
        json={"name": "Leite sem lactose", "quantity": "2", "unit": "l", "category": "bebidas"},
    )
    assert resp.status_code == 200
    edited = resp.json()["items"][0]
    assert (edited["name"], edited["quantity"], edited["unit"], edited["category"]) == (
        "Leite sem lactose",
        "2",
        "l",
        "bebidas",
    )
    assert client.patch(f"{ITEMS}/{item_id}", json={"category": "inventada"}).status_code == 422
    assert client.patch(f"{ITEMS}/{item_id}", json={"quantity": "0"}).status_code == 422
    assert client.patch(f"{ITEMS}/{item_id}", json={"name": "   "}).status_code == 422


@pytest.mark.req("F-304")
def test_the_expected_price_is_the_last_one_paid_net_of_discount_not_the_average_or_the_lowest(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    coffee = product("Café moído")
    buy(user, coffee, TODAY - timedelta(days=60), 389)  # a promotion, the lowest
    buy(user, coffee, TODAY - timedelta(days=30), 589)
    buy(user, coffee, TODAY - timedelta(days=10), 600, discount=100)  # 5,00 € net, the last
    buy(user, coffee, TODAY - timedelta(days=2), 999, status=ReceiptStatus.REVIEW_REQUIRED)  # not confirmed
    client.put(f"{CURRENT}/merchant", json={"merchant": "LIDL"})
    expected = add(client, "Café moído")["items"][0]["expected"]
    assert expected["price_cents"] == 500
    assert expected["price_basis"] == "un"
    assert expected["date"] == (TODAY - timedelta(days=10)).isoformat()
    assert expected["other_merchant"] is False
    assert expected["source"] == "own_receipts"


@pytest.mark.req("F-304")
def test_a_weighed_product_is_priced_per_kilo(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    chicken = product("Peito de frango")
    buy(user, chicken, TODAY - timedelta(days=3), 1100, qty="2", unit="kg")
    expected = add(client, "Peito de frango")["items"][0]["expected"]
    assert (expected["price_cents"], expected["price_basis"]) == (550, "kg")


@pytest.mark.req("F-305")
def test_without_a_price_at_the_chosen_supermarket_the_last_one_paid_elsewhere_is_shown_as_such(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    rice = product("Arroz agulha")
    buy(user, rice, TODAY - timedelta(days=40), 119, merchant=Merchant.LIDL)
    buy(user, rice, TODAY - timedelta(days=9), 129, merchant=Merchant.CONTINENTE)
    buy(user, rice, TODAY - timedelta(days=4), 139, merchant="UNKNOWN", merchant_name="Mercearia do Zé")
    add(client, "Arroz agulha")
    client.put(f"{CURRENT}/merchant", json={"merchant": "LIDL"})
    own = the_list(client)["items"][0]["expected"]
    assert (own["price_cents"], own["merchant"], own["other_merchant"]) == (119, "LIDL", False)
    client.put(f"{CURRENT}/merchant", json={"merchant": "PINGO_DOCE"})
    other = the_list(client)["items"][0]["expected"]
    # The most recent price anywhere, with the store it came from, never passed off as the chosen one.
    assert (other["price_cents"], other["merchant"], other["merchant_name"], other["other_merchant"]) == (
        139,
        None,
        "Mercearia do Zé",
        True,
    )


@pytest.mark.req("F-305")
def test_a_product_never_bought_has_no_price(client: TestClient, user: User) -> None:
    assert add(client, "Pão de forma")["items"][0]["expected"] is None


@pytest.mark.req("F-306")
def test_the_estimate_counts_what_is_not_already_at_home_unless_it_is_in_the_cart(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    for name, cents in (("Leite", 82), ("Ovos", 289), ("Arroz", 119), ("Café", 494)):
        buy(user, product(name), TODAY - timedelta(days=5), cents)
    for name in ("Leite", "Ovos", "Arroz"):
        add(client, name)
    add(client, "Café", already_have=True)
    add(client, "Pão de forma")
    client.put(f"{CURRENT}/merchant", json={"merchant": "LIDL"})
    body = the_list(client)
    assert estimate(body, "LIDL") == {
        "merchant": "LIDL",
        "merchant_name": "Lidl",
        "total_cents": 82 + 289 + 119,
        "items_with_price": 3,
        "other_merchant_values": 0,
    }
    assert (body["items_total"], body["checked_count"], body["already_have_excluded"]) == (5, 0, 1)
    # Putting the one already at home in the cart makes it count.
    coffee = item_named(body, "Café")["id"]
    body = client.patch(f"{ITEMS}/{coffee}", json={"checked": True}).json()
    assert estimate(body, "LIDL")["total_cents"] == 82 + 289 + 119 + 494
    assert (body["checked_count"], body["already_have_excluded"]) == (1, 0)
    # «Já tenho» and the cart are two different things: turning one off leaves the other as it was.
    body = client.patch(f"{ITEMS}/{coffee}", json={"already_have": False}).json()
    assert item_named(body, "Café")["checked"] is True


@pytest.mark.req("F-306")
def test_the_estimate_multiplies_by_the_quantity_and_counts_the_values_from_other_supermarkets(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    buy(user, product("Leite"), TODAY - timedelta(days=5), 82, merchant=Merchant.LIDL)
    buy(user, product("Ovos"), TODAY - timedelta(days=5), 289, merchant=Merchant.CONTINENTE)
    add(client, "Leite", quantity="6")
    add(client, "Ovos")
    body = the_list(client)
    assert estimate(body, "LIDL") == {
        "merchant": "LIDL",
        "merchant_name": "Lidl",
        "total_cents": 6 * 82 + 289,
        "items_with_price": 2,
        "other_merchant_values": 1,
    }
    assert estimate(body, "CONTINENTE")["other_merchant_values"] == 1


@pytest.mark.req("F-307")
def test_a_removed_item_can_be_put_back_in_the_same_place(client: TestClient, user: User) -> None:
    add(client, "Leite")
    ids = {n: add(client, n)["items"][-1]["id"] for n in ("Iogurte", "Queijo")}
    before = [i["name"] for i in the_list(client)["items"]]
    removed = client.delete(f"{ITEMS}/{ids['Iogurte']}")
    assert removed.status_code == 200
    assert "Iogurte" not in [i["name"] for i in removed.json()["items"]]
    assert removed.json()["items_total"] == 2
    restored = client.post(f"{ITEMS}/{ids['Iogurte']}/restore")
    assert restored.status_code == 200
    assert [i["name"] for i in restored.json()["items"]] == before
    assert client.post(f"{ITEMS}/{ids['Iogurte']}/restore").status_code == 404  # not removed


@pytest.mark.req("F-307")
def test_a_removed_item_cannot_be_edited_and_is_forgotten_after_the_undo_window(
    client: TestClient, user: User, db: Session
) -> None:
    item_id = add(client, "Leite")["items"][0]["id"]
    client.delete(f"{ITEMS}/{item_id}")
    assert client.patch(f"{ITEMS}/{item_id}", json={"checked": True}).status_code == 404
    db.execute(
        update(ShoppingListItem)
        .where(ShoppingListItem.id == uuid.UUID(item_id))
        .values(deleted_at=datetime.now(UTC) - timedelta(days=2))
    )
    db.commit()
    the_list(client)
    db.expire_all()
    assert db.scalar(select(ShoppingListItem).where(ShoppingListItem.id == uuid.UUID(item_id))) is None
    assert client.post(f"{ITEMS}/{item_id}/restore").status_code == 404


@pytest.mark.req("F-308")
def test_one_account_never_sees_or_touches_the_list_of_another(
    client: TestClient, login: Callable[..., User]
) -> None:
    login(client)
    item_id = add(client, "Leite")["items"][0]["id"]
    with TestClient(client.app, base_url="http://testserver") as other:
        login(other)
        assert the_list(other)["items"] == []
        assert other.patch(f"{ITEMS}/{item_id}", json={"checked": True}).status_code == 404
        assert other.delete(f"{ITEMS}/{item_id}").status_code == 404
        assert other.get(f"{ITEMS}/{item_id}/evolution").status_code == 404


@pytest.mark.req("F-308")
def test_deleting_the_account_deletes_the_list(client: TestClient, user: User, db: Session) -> None:
    add(client, "Leite")
    assert db.scalar(select(ShoppingList).where(ShoppingList.user_id == user.id)) is not None
    assert client.delete("/v1/me").status_code == 200
    db.expire_all()
    assert db.scalar(select(ShoppingList).where(ShoppingList.user_id == user.id)) is None
    assert db.scalar(select(ShoppingListItem)) is None


@pytest.mark.req("F-309")
def test_the_evolution_lists_every_purchase_at_every_supermarket(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    coffee = product("Café moído")
    buy(user, coffee, TODAY - timedelta(days=100), 589, merchant=Merchant.LIDL)
    buy(user, coffee, TODAY - timedelta(days=40), 399, merchant=Merchant.LIDL)
    buy(user, coffee, TODAY - timedelta(days=12), 494, merchant=Merchant.CONTINENTE)
    item_id = add(client, "Café moído")["items"][0]["id"]
    client.put(f"{CURRENT}/merchant", json={"merchant": "LIDL"})
    body = client.get(f"{ITEMS}/{item_id}/evolution?months=6").json()
    assert body["has_history"] is True
    assert [(p["price_cents"], p["merchant"], p["origin"]) for p in body["points"]] == [
        (589, "LIDL", "own"),
        (399, "LIDL", "own"),
        (494, "CONTINENTE", "own"),
    ]
    assert (body["last_cents"], body["min_cents"], body["max_cents"], body["price_basis"]) == (
        494,
        399,
        589,
        "un",
    )
    assert body["expected"]["price_cents"] == 399  # the last one paid at the chosen supermarket
    short = client.get(f"{ITEMS}/{item_id}/evolution?months=1").json()
    assert [p["price_cents"] for p in short["points"]] == [494]


@pytest.mark.req("F-309")
def test_the_evolution_falls_back_to_older_purchases_when_the_window_has_none(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    buy(user, product("Arroz agulha"), TODAY - timedelta(days=300), 119)
    item_id = add(client, "Arroz agulha")["items"][0]["id"]
    body = client.get(f"{ITEMS}/{item_id}/evolution?months=3").json()
    assert body["has_history"] is True
    assert len(body["points"]) == 1


@pytest.mark.req("F-309")
def test_an_item_with_nothing_bought_has_no_history_and_no_numbers(client: TestClient, user: User) -> None:
    item_id = add(client, "Pão de forma")["items"][0]["id"]
    assert client.get(f"{ITEMS}/{item_id}/evolution").json() == {
        "has_history": False,
        "price_basis": None,
        "expected": None,
        "last_cents": None,
        "min_cents": None,
        "max_cents": None,
        "points": [],
    }


@pytest.mark.req("F-310")
def test_the_suggestions_are_what_is_bought_often_and_is_not_already_on_the_list(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    assert client.get(f"{CURRENT}/suggestions").json() == []
    yoghurt, bananas, soap = product("Iogurte natural"), product("Bananas"), product("Sabão")
    for n in range(3):
        buy(user, yoghurt, TODAY - timedelta(days=5 + n * 7), 79)
    for n in range(2):
        buy(user, bananas, TODAY - timedelta(days=6 + n * 7), 120)
    buy(user, soap, TODAY - timedelta(days=9), 300)
    first = client.get(f"{CURRENT}/suggestions").json()
    assert [s["name"] for s in first] == ["Iogurte natural", "Bananas", "Sabão"]
    assert first[0]["category_name"] == "Laticínios e ovos"
    add(client, "Iogurte natural")
    assert [s["name"] for s in client.get(f"{CURRENT}/suggestions").json()] == ["Bananas", "Sabão"]


@pytest.mark.req("F-306")
def test_the_estimate_converts_the_items_unit_to_the_unit_of_the_price(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:
    buy(user, product("Bananas"), TODAY - timedelta(days=5), 150, qty="1.000", unit="kg")  # 1,50 €/kg
    buy(user, product("Queijo"), TODAY - timedelta(days=5), 500, qty="0.500", unit="kg")  # 10,00 €/kg
    add(client, "Bananas", quantity="500", unit="g")  # 0,5 kg
    add(client, "Queijo", quantity="2", unit="un")  # two of what? Not comparable with a price per kg
    body = the_list(client)
    assert estimate(body, "LIDL")["total_cents"] == 75
    assert estimate(body, "LIDL")["items_with_price"] == 1
    # The item keeps its price per kg on the list, even when it cannot enter the estimate.
    assert item_named(body, "Queijo")["expected"]["price_cents"] == 1000


@pytest.mark.req("F-311")
def test_the_category_follows_the_product_group_of_step_c(
    client: TestClient, user: User, product: Callable[[str], Product], buy: Callable[..., None]
) -> None:

    def category_of(name: str, **fields: Any) -> str:
        return str(item_named(add(client, name, **fields), name)["category"])

    # The catalogue knows these; the keywords alone would get them wrong.
    assert category_of("Peito de peru fatiado") == "charcutaria"
    assert category_of("Salada iceberg") == "frutas_legumes"
    assert category_of("Molho de tomate") == "mercearia"  # a sauce, not a vegetable
    assert category_of("Bolachas água e sal") == "mercearia"  # biscuits, not a drink
    # A product the person grouped herself takes the category of that group, in the suggestions too.
    buy(user, product("Artigo da padaria"), TODAY - timedelta(days=5), 129)
    rule = {"targets": [{"chain": "LIDL", "description": "ARTIGO"}], "group": "pao-de-forma"}
    assert client.put("/v1/line-rules", json=rule).status_code == 200
    suggested = client.get(f"{CURRENT}/suggestions").json()
    assert [(s["name"], s["category"]) for s in suggested] == [("Artigo da padaria", "padaria")]
    assert category_of("Artigo da padaria") == "padaria"
    # A category chosen by the person always wins.
    assert category_of("Salada de fruta", category="mercearia") == "mercearia"
