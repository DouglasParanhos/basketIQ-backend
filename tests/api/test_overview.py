"""Home overview and month totals (RF-15, spec 014): GET /v1/analytics/overview and /v1/analytics/months."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from basketiq.app_config import AppConfig, get_app_config
from basketiq.db.models import AuditEvent, Receipt, ReceiptLine, User, UserProfile
from basketiq.domain.status import ReceiptStatus
from basketiq.observability.logging import configure_logging

LISBON = ZoneInfo("Europe/Lisbon")
OVERVIEW = "/v1/analytics/overview"
MONTHS = "/v1/analytics/months"
TODAY = date(2026, 11, 15)  # the day the tests pretend it is: October 2026 is the last complete month


@pytest.fixture(autouse=True)
def today() -> Iterator[None]:
    from basketiq.api.deps import get_today
    from basketiq.api.main import app

    app.dependency_overrides[get_today] = lambda: TODAY
    yield
    app.dependency_overrides.pop(get_today, None)


@pytest.fixture
def user(client: TestClient, login: Callable[..., User]) -> User:
    return login(client)


@pytest.fixture
def add(db: Session) -> Callable[..., Receipt]:
    """Insert a receipt of `owner` straight into the database (no parsing involved)."""

    def _add(
        owner: User,
        at: datetime | None,
        total: int | None = None,
        *,
        merchant: str | None = "LIDL",
        merchant_name: str | None = None,
        status: ReceiptStatus = ReceiptStatus.CONFIRMED,
        lines: list[tuple[int, int]] | None = None,
        created_at: datetime | None = None,
    ) -> Receipt:
        """`lines` are (line total, discount) pairs; `at` is naive Lisbon time."""
        rid = uuid.uuid4()
        receipt = Receipt(
            id=rid,
            user_id=owner.id,
            merchant=merchant,
            merchant_name=merchant_name,
            purchase_at=at.replace(tzinfo=LISBON) if at else None,
            source_type="image",
            content_type="image/png",
            size_bytes=1,
            object_key=f"originals/{owner.id}/{rid}",
            processing_status=status,
            total_cents=total,
        )
        if created_at:
            receipt.created_at = created_at
        db.add(receipt)
        db.flush()
        for n, (line_total, discount) in enumerate(lines or [], start=1):
            db.add(
                ReceiptLine(
                    receipt_id=rid,
                    line_number=n,
                    raw_description=f"ARTIGO {n}",
                    quantity_decimal=Decimal(1),
                    unit="un",
                    line_total_cents=line_total,
                    discount_cents=discount,
                )
            )
        db.commit()
        return receipt

    return _add


@pytest.fixture
def goal(db: Session) -> Callable[[User, int | None], None]:
    def _goal(owner: User, cents: int | None) -> None:
        db.merge(UserProfile(user_id=owner.id, monthly_goal_cents=cents))
        db.commit()

    return _goal


def october(client: TestClient, month: str = "2026-10") -> dict[str, Any]:
    resp = client.get(f"{OVERVIEW}?month={month}")
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


# ---- US1: spending, goal and band --------------------------------------------------------------------------


@pytest.mark.req("F-176")
@pytest.mark.parametrize(
    ("spent", "pct", "band"), [(15_200, 38, "ok"), (24_800, 62, "attention"), (35_200, 88, "high")]
)
def test_the_ring_has_three_bands_from_the_goal(
    client: TestClient, user: User, add: Any, goal: Any, spent: int, pct: int, band: str
) -> None:
    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), spent)
    body = october(client)
    assert (body["spent_cents"], body["goal_cents"], body["goal_pct"], body["band"]) == (
        spent,
        40_000,
        pct,
        band,
    )
    assert body["month"] == "2026-10"


@pytest.mark.req("F-176")
@pytest.mark.parametrize("profile", ["none", "no_goal"])
def test_without_a_goal_the_spending_is_still_reported(
    client: TestClient, user: User, add: Any, goal: Any, profile: str
) -> None:
    if profile == "no_goal":
        goal(user, None)
    add(user, datetime(2026, 10, 3, 10, 0), 12_345)
    body = october(client)
    assert body["spent_cents"] == 12_345
    assert (body["goal_cents"], body["goal_pct"], body["band"]) == (None, None, "none")


@pytest.mark.req("F-176")
def test_spending_past_the_goal_is_not_an_error(client: TestClient, user: User, add: Any, goal: Any) -> None:
    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), 52_000)
    body = october(client)
    assert (body["goal_pct"], body["band"]) == (130, "high")


@pytest.mark.req("F-176")
@pytest.mark.parametrize(
    ("spent", "pct", "band"),
    [
        (20_000, 50, "ok"),
        (20_160, 50, "ok"),
        (20_200, 51, "attention"),
        (30_000, 75, "attention"),
        (30_160, 75, "attention"),
        (30_200, 76, "high"),
    ],
)
def test_band_boundaries_use_the_rounded_percentage(
    client: TestClient, user: User, add: Any, goal: Any, spent: int, pct: int, band: str
) -> None:
    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), spent)
    body = october(client)
    assert (body["goal_pct"], body["band"]) == (pct, band)


@pytest.mark.req("F-176")
def test_the_limits_come_from_the_configuration_file(
    client: TestClient, user: User, add: Any, goal: Any
) -> None:
    from basketiq.api.main import app
    from basketiq.app_config import DEFAULT_PATH, load_app_config

    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), 20_200)  # 51 %
    assert october(client)["band"] == "attention"
    values = load_app_config(DEFAULT_PATH).model_dump(by_alias=True)
    values.update(GOAL_RING_GREEN_UP_TO_PCT=60, GOAL_RING_ORANGE_UP_TO_PCT=80)
    cfg = AppConfig.model_validate(values)
    app.dependency_overrides[get_app_config] = lambda: cfg
    try:
        assert october(client)["band"] == "ok"
    finally:
        app.dependency_overrides.pop(get_app_config, None)


@pytest.mark.req("F-176")
def test_only_confirmed_receipts_count(client: TestClient, user: User, add: Any) -> None:
    at = datetime(2026, 10, 3, 10, 0)
    add(user, at, 1_000)
    for status in (ReceiptStatus.REVIEW_REQUIRED, ReceiptStatus.COMPLETED, ReceiptStatus.RECEIVED):
        add(user, at, 7_777, status=status)
    body = october(client)
    assert (body["spent_cents"], body["purchases_this_month"]) == (1_000, 1)
    assert sum(w["spent_cents"] for w in body["weeks"]) == 1_000
    assert [m["spent_cents"] for m in body["by_merchant"]] == [1_000]


@pytest.mark.req("F-176")
def test_a_receipt_with_only_a_total_counts(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 10, 3, 10, 0), 2_345, lines=[])  # no lines, as a weighed-scale purchase
    assert october(client)["spent_cents"] == 2_345


@pytest.mark.req("F-176")
def test_a_receipt_without_a_total_adds_up_its_lines_net_of_discounts(
    client: TestClient, user: User, add: Any
) -> None:
    add(user, datetime(2026, 10, 3, 10, 0), None, lines=[(500, 0), (300, 50)])
    add(user, datetime(2026, 10, 4, 10, 0), 1_000, lines=[(400, 0)])  # the printed total wins over the lines
    assert october(client)["spent_cents"] == 750 + 1_000


@pytest.mark.req("F-176")
def test_the_month_is_the_one_in_lisbon(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 10, 31, 23, 30), 100)  # still October
    add(user, datetime(2026, 11, 1, 0, 30), 250)  # 23:30 UTC on 31 October, but November in Lisbon
    assert october(client, "2026-10")["spent_cents"] == 100
    assert october(client, "2026-11")["spent_cents"] == 250


@pytest.mark.req("F-176")
@pytest.mark.parametrize(
    "month", ["2026-13", "2026-00", "abc", "2026-1", "2026-10-01", "26-10", "1999-12", "2100-01"]
)
def test_an_invalid_month_is_a_validation_error(client: TestClient, user: User, month: str) -> None:
    resp = client.get(f"{OVERVIEW}?month={month}")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert [e["field"] for e in body["field_errors"]] == ["month"]


@pytest.mark.req("F-176")
def test_without_a_month_it_is_the_current_month_in_lisbon(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 11, 2, 10, 0), 600)  # TODAY is in November
    add(user, datetime(2026, 10, 2, 10, 0), 900)
    body = client.get(OVERVIEW).json()
    assert (body["month"], body["spent_cents"]) == ("2026-11", 600)


@pytest.mark.req("F-176")
def test_a_future_month_is_zeros_not_an_error(client: TestClient, user: User, add: Any, goal: Any) -> None:
    goal(user, 40_000)
    add(user, datetime(2026, 10, 2, 10, 0), 900)
    body = october(client, "2027-03")
    assert (body["spent_cents"], body["goal_pct"], body["band"]) == (0, 0, "ok")
    assert (body["purchases_this_month"], body["by_merchant"], body["recent_receipts"]) == (0, [], [])
    assert [w["spent_cents"] for w in body["weeks"]] == [0, 0, 0, 0, 0]
    assert body["empty"] is False


@pytest.mark.req("F-176")
def test_the_privacy_consent_of_the_current_policy_is_required(
    client: TestClient, login: Callable[..., User]
) -> None:
    other = TestClient(client.app)
    login(other, consent=False)
    resp = other.get(OVERVIEW)
    assert resp.status_code == 403 and resp.json()["code"] == "privacy_consent_required"


# ---- US2: monthly average and trend


def months_of_spending(add: Any, owner: User, amounts: dict[str, int]) -> None:
    for month, cents in amounts.items():
        year, mo = month.split("-")
        add(owner, datetime(int(year), int(mo), 10, 12, 0), cents)


@pytest.mark.req("F-177")
def test_two_complete_months_give_an_average_but_no_trend(client: TestClient, user: User, add: Any) -> None:
    months_of_spending(add, user, {"2026-09": 30_000, "2026-10": 33_600})
    body = october(client)
    assert body["monthly_average_cents"] == 31_800
    assert (body["recent_vs_average_pct"], body["average_trend"]) == (None, None)
    assert (body["average_months"], body["average_min_months"]) == (2, 2)


@pytest.mark.req("F-177")
def test_one_month_is_not_enough_for_an_average(client: TestClient, user: User, add: Any) -> None:
    months_of_spending(add, user, {"2026-10": 33_600})
    body = october(client)
    assert (body["monthly_average_cents"], body["recent_vs_average_pct"]) == (None, None)
    assert (body["average_months"], body["average_min_months"]) == (1, 2)


@pytest.mark.req("F-177")
def test_four_months_give_the_trend_with_its_sign(client: TestClient, user: User, add: Any) -> None:
    months_of_spending(
        add, user, {"2026-07": 36_000, "2026-08": 30_000, "2026-09": 26_000, "2026-10": 28_000}
    )
    body = october(client)
    assert body["monthly_average_cents"] == 30_000
    assert (body["recent_vs_average_pct"], body["average_trend"]) == (-10, "below")
    assert body["average_months"] == 4


@pytest.mark.req("F-177")
def test_the_current_month_is_left_out_of_the_average(client: TestClient, user: User, add: Any) -> None:
    months_of_spending(add, user, {"2026-09": 30_000, "2026-10": 33_600})
    before = october(client)["monthly_average_cents"]
    months_of_spending(add, user, {"2026-11": 90_000})  # TODAY is in November: incomplete
    assert october(client)["monthly_average_cents"] == before == 31_800
    assert (
        october(client, "2026-11")["monthly_average_cents"] == 31_800
    )  # it does not depend on the month asked


@pytest.mark.req("F-177")
def test_an_empty_month_between_two_months_does_not_count_as_zero(
    client: TestClient, user: User, add: Any
) -> None:
    months_of_spending(add, user, {"2026-08": 30_000, "2026-10": 33_600})  # nothing in September
    body = october(client)
    assert (body["monthly_average_cents"], body["average_months"]) == (31_800, 2)


@pytest.mark.req("F-177")
def test_the_average_and_the_goal_do_not_depend_on_each_other(
    client: TestClient, user: User, add: Any, goal: Any
) -> None:
    months_of_spending(add, user, {"2026-09": 30_000, "2026-10": 33_600})
    body = october(client)
    assert (body["goal_cents"], body["monthly_average_cents"]) == (
        None,
        31_800,
    )  # no goal: average still there
    goal(user, 40_000)
    body = october(client)
    assert (body["goal_cents"], body["monthly_average_cents"]) == (40_000, 31_800)  # a goal never replaces it


@pytest.mark.req("F-177")
def test_unconfirmed_receipts_stay_out_of_the_average(client: TestClient, user: User, add: Any) -> None:
    months_of_spending(add, user, {"2026-09": 30_000, "2026-10": 33_600})
    add(user, datetime(2026, 8, 10, 12, 0), 99_999, status=ReceiptStatus.REVIEW_REQUIRED)
    body = october(client)
    assert (body["monthly_average_cents"], body["average_months"]) == (31_800, 2)


# ---- US3: purchases, weeks, merchants and recent receipts


@pytest.mark.req("F-178")
def test_purchases_this_month_and_the_month_before(client: TestClient, user: User, add: Any) -> None:
    for day in range(1, 8):
        add(user, datetime(2026, 10, day, 12, 0), 100)
    for day in range(1, 12):
        add(user, datetime(2026, 9, day, 12, 0), 100)
    body = october(client)
    assert (body["purchases_this_month"], body["purchases_last_month"]) == (7, 11)


@pytest.mark.req("F-178")
def test_weeks_add_up_to_the_month_and_empty_weeks_are_zero(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 10, 7, 23, 0), 100)  # last day of week 1
    add(user, datetime(2026, 10, 8, 0, 30), 200)  # first day of week 2
    add(user, datetime(2026, 10, 28, 12, 0), 300)  # last day of week 4
    add(user, datetime(2026, 10, 29, 12, 0), 400)  # first day of week 5
    weeks = october(client)["weeks"]
    assert [w["index"] for w in weeks] == [1, 2, 3, 4, 5]
    assert [w["spent_cents"] for w in weeks] == [100, 200, 0, 300, 400]
    assert (weeks[0]["from"], weeks[0]["to"]) == ("2026-10-01", "2026-10-07")
    assert (weeks[4]["from"], weeks[4]["to"]) == ("2026-10-29", "2026-10-31")
    assert sum(w["spent_cents"] for w in weeks) == 1_000


@pytest.mark.req("F-178")
def test_a_28_day_month_has_four_weeks(client: TestClient, user: User) -> None:
    assert len(october(client, "2027-02")["weeks"]) == 4


@pytest.mark.req("F-178")
def test_merchants_by_spending_with_shares_that_add_up_to_one_hundred(
    client: TestClient, user: User, add: Any
) -> None:
    at = datetime(2026, 10, 3, 10, 0)
    add(user, at, 5_000, merchant="PINGO_DOCE")
    add(user, at, 3_000, merchant="LIDL")
    add(user, at, 1_000, merchant="LIDL")
    add(user, at, 1_000, merchant="ALDI")
    shares = october(client)["by_merchant"]
    assert [(m["merchant"], m["name"], m["spent_cents"], m["purchases"]) for m in shares] == [
        ("PINGO_DOCE", "Pingo Doce", 5_000, 1),
        ("LIDL", "Lidl", 4_000, 2),
        ("ALDI", "Aldi", 1_000, 1),
    ]
    assert [m["share_pct"] for m in shares] == [50, 40, 10]
    assert sum(m["share_pct"] for m in shares) == 100


@pytest.mark.req("F-178")
def test_shares_are_rounded_so_they_add_up_to_one_hundred(client: TestClient, user: User, add: Any) -> None:
    at = datetime(2026, 10, 3, 10, 0)
    for merchant in ("LIDL", "ALDI", "AUCHAN"):
        add(user, at, 1_000, merchant=merchant)
    shares = october(client)["by_merchant"]
    assert sorted(m["share_pct"] for m in shares) == [33, 33, 34]


@pytest.mark.req("F-178")
def test_a_store_without_its_own_reader_is_listed_by_the_name_it_has(
    client: TestClient, user: User, add: Any
) -> None:
    at = datetime(2026, 10, 3, 10, 0)
    add(user, at, 3_000, merchant="UNKNOWN", merchant_name="Mercearia do Zé")
    add(user, at, 2_000, merchant="UNKNOWN", merchant_name="Talho Central")
    add(user, at, 1_000, merchant=None, merchant_name=None)
    shares = october(client)["by_merchant"]
    assert [(m["merchant"], m["name"], m["share_pct"]) for m in shares] == [
        (None, "Mercearia do Zé", 50),
        (None, "Talho Central", 33),
        (None, "Outros", 17),
    ]


@pytest.mark.req("F-178")
def test_the_five_latest_receipts_of_the_month(client: TestClient, user: User, add: Any) -> None:
    for day in range(1, 8):
        add(user, datetime(2026, 10, day, 12, 0), day * 100, merchant="LIDL")
    add(user, datetime(2026, 9, 30, 12, 0), 9_999)  # another month
    recent = october(client)["recent_receipts"]
    assert len(recent) == 5
    assert [r["date"] for r in recent] == [
        "2026-10-07",
        "2026-10-06",
        "2026-10-05",
        "2026-10-04",
        "2026-10-03",
    ]
    first = recent[0]
    assert (first["merchant"], first["name"], first["total_cents"], first["status"]) == (
        "LIDL",
        "Lidl",
        700,
        "CONFIRMED",
    )
    assert uuid.UUID(first["id"])


@pytest.mark.req("F-178")
def test_a_receipt_at_midnight_shows_the_lisbon_date(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 11, 1, 0, 30), 100)  # 31 October at 23:30 UTC
    assert october(client, "2026-11")["recent_receipts"][0]["date"] == "2026-11-01"


@pytest.mark.req("F-178")
def test_a_new_account_is_empty_and_an_account_with_other_months_is_not(
    client: TestClient, user: User, add: Any
) -> None:
    assert october(client)["empty"] is True
    add(user, datetime(2026, 9, 3, 10, 0), 500)
    body = october(client)  # nothing in October
    assert body["empty"] is False
    assert (body["spent_cents"], body["purchases_this_month"], body["purchases_last_month"]) == (0, 0, 1)


@pytest.mark.req("F-178")
def test_unconfirmed_receipts_alone_leave_the_account_empty(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 10, 3, 10, 0), 500, status=ReceiptStatus.REVIEW_REQUIRED)
    assert october(client)["empty"] is True


@pytest.mark.req("F-178")
def test_a_scale_receipt_without_lines_counts_everywhere(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 10, 3, 10, 0), 640, lines=[])
    body = october(client)
    assert body["purchases_this_month"] == 1
    assert [w["spent_cents"] for w in body["weeks"]] == [640, 0, 0, 0, 0]
    assert [m["spent_cents"] for m in body["by_merchant"]] == [640]
    assert body["recent_receipts"][0]["total_cents"] == 640


@pytest.mark.req("F-178")
def test_recent_receipts_also_show_those_to_review_or_being_processed(
    client: TestClient, user: User, add: Any
) -> None:
    shown = {
        ReceiptStatus.CONFIRMED: 1,
        ReceiptStatus.REVIEW_REQUIRED: 2,
        ReceiptStatus.COMPLETED: 3,
        ReceiptStatus.RECEIVED: 4,
        ReceiptStatus.VALIDATING: 5,
    }
    for status, day in shown.items():
        add(user, datetime(2026, 10, day, 12, 0), 100 * day, status=status)
    recent = october(client)["recent_receipts"]
    assert [r["status"] for r in recent] == [
        "VALIDATING",
        "RECEIVED",
        "COMPLETED",
        "REVIEW_REQUIRED",
        "CONFIRMED",
    ]
    add(user, datetime(2026, 10, 6, 12, 0), 600, status=ReceiptStatus.EXTRACTING)
    assert october(client)["recent_receipts"][0]["status"] == "EXTRACTING"


@pytest.mark.req("F-178")
@pytest.mark.parametrize(
    "status",
    [
        ReceiptStatus.INVALID_FORMAT,
        ReceiptStatus.UNREADABLE,
        ReceiptStatus.UNSUPPORTED_MERCHANT,
        ReceiptStatus.TEMPORARY_FAILURE,
        ReceiptStatus.PERMANENT_FAILURE,
    ],
)
def test_failed_receipts_never_appear_in_the_recent_list(
    client: TestClient, user: User, add: Any, status: ReceiptStatus
) -> None:
    add(user, datetime(2026, 10, 3, 12, 0), 100, status=status)
    assert october(client)["recent_receipts"] == []


@pytest.mark.req("F-178")
def test_a_receipt_still_being_read_uses_the_day_it_arrived_and_has_no_invented_values(
    client: TestClient, user: User, add: Any
) -> None:
    arrived = datetime(2026, 10, 20, 9, 0, tzinfo=LISBON)
    add(user, None, None, merchant=None, status=ReceiptStatus.EXTRACTING, created_at=arrived)
    item = october(client)["recent_receipts"][0]
    assert (item["date"], item["merchant"], item["name"], item["total_cents"], item["status"]) == (
        "2026-10-20",
        None,
        "Outros",
        None,
        "EXTRACTING",
    )
    # and it is counted in no total
    assert october(client)["spent_cents"] == 0
    assert october(client, "2026-11")["recent_receipts"] == []


@pytest.mark.req("F-178")
def test_unconfirmed_totals_are_the_value_read_never_the_sum_of_lines(
    client: TestClient, user: User, add: Any
) -> None:
    add(user, datetime(2026, 10, 3, 12, 0), None, status=ReceiptStatus.REVIEW_REQUIRED, lines=[(500, 0)])
    assert october(client)["recent_receipts"][0]["total_cents"] is None


# ---- US4: totals by month


@pytest.mark.req("F-179")
def test_month_totals_count_confirmed_receipts_newest_month_first(
    client: TestClient, user: User, add: Any
) -> None:
    for day in range(1, 8):
        add(user, datetime(2026, 10, day, 12, 0), 3_800 if day == 1 else 3_000)
    add(user, datetime(2026, 9, 5, 12, 0), 1_000)
    add(user, datetime(2026, 7, 5, 12, 0), 2_000)  # August has none: it does not appear
    add(user, datetime(2026, 10, 9, 12, 0), 5_555, status=ReceiptStatus.REVIEW_REQUIRED)
    resp = client.get(MONTHS)
    assert resp.status_code == 200
    assert resp.json() == [
        {"month": "2026-10", "confirmed_count": 7, "total_cents": 7 * 3_000 + 800},
        {"month": "2026-09", "confirmed_count": 1, "total_cents": 1_000},
        {"month": "2026-07", "confirmed_count": 1, "total_cents": 2_000},
    ]


@pytest.mark.req("F-179")
def test_month_totals_use_the_same_month_as_the_overview(client: TestClient, user: User, add: Any) -> None:
    add(user, datetime(2026, 11, 1, 0, 30), 250)
    add(user, datetime(2026, 10, 31, 23, 30), 100)
    assert client.get(MONTHS).json() == [
        {"month": "2026-11", "confirmed_count": 1, "total_cents": 250},
        {"month": "2026-10", "confirmed_count": 1, "total_cents": 100},
    ]
    assert october(client, "2026-11")["spent_cents"] == 250


@pytest.mark.req("F-179")
def test_month_totals_of_an_account_without_receipts_are_empty(client: TestClient, user: User) -> None:
    assert client.get(MONTHS).json() == []


@pytest.mark.req("F-179")
def test_month_totals_need_a_session_and_the_current_consent(
    client: TestClient, login: Callable[..., User]
) -> None:
    assert client.get(MONTHS).status_code == 401
    login(client, consent=False)
    resp = client.get(MONTHS)
    assert resp.status_code == 403 and resp.json()["code"] == "privacy_consent_required"


# ---- US6: isolation, privacy and edge cases


@pytest.mark.req("F-181")
@pytest.mark.req("PT-AC-9")
@pytest.mark.req("SEC-authz")
def test_another_user_never_sees_these_data(
    client: TestClient, user: User, login: Callable[..., User], add: Any, goal: Any
) -> None:
    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), 11_111, merchant="PINGO_DOCE")
    other = TestClient(client.app)
    login(other)
    body = other.get(f"{OVERVIEW}?month=2026-10").json()
    assert (body["spent_cents"], body["goal_cents"], body["by_merchant"], body["recent_receipts"]) == (
        0,
        None,
        [],
        [],
    )
    assert body["empty"] is True
    assert other.get(MONTHS).json() == []


@pytest.mark.req("F-181")
@pytest.mark.req("SEC-authz")
def test_the_overview_needs_a_session(client: TestClient) -> None:
    assert client.get(OVERVIEW).status_code == 401


@pytest.mark.req("F-181")
def test_a_user_without_a_profile_always_gets_a_valid_answer(
    client: TestClient, user: User, db: Session
) -> None:
    assert db.get(UserProfile, user.id) is None
    body = client.get(OVERVIEW).json()
    assert (body["goal_cents"], body["band"], body["spent_cents"], body["empty"]) == (None, "none", 0, True)


@pytest.mark.req("F-181")
@pytest.mark.req("SEC-logs")
def test_values_reach_neither_the_logs_nor_the_error_bodies(
    client: TestClient, user: User, add: Any, goal: Any, db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    goal(user, 43_210)
    add(user, datetime(2026, 10, 3, 10, 0), 98_765, merchant="UNKNOWN", merchant_name="LOJA_SECRETA_XYZ")
    assert client.get(f"{OVERVIEW}?month=2026-10").status_code == 200
    bad = client.get(f"{OVERVIEW}?month=2026-13")
    assert bad.status_code == 422
    assert client.get(MONTHS).status_code == 200
    logs = capsys.readouterr().out
    assert "request" in logs  # the requests were logged, without their content
    for private in ("98765", "43210", "LOJA_SECRETA_XYZ", "monthly_goal"):
        assert private not in logs
    assert "98765" not in str(bad.json()) and "43210" not in str(bad.json())  # the error echoes no amount
    audit = " ".join(str(e.payload_redacted) for e in db.scalars(select(AuditEvent)).all())
    assert "98765" not in audit and "43210" not in audit


@pytest.mark.req("F-181")
def test_metric_labels_carry_no_values(client: TestClient, user: User) -> None:
    from basketiq.observability import metrics

    client.get(OVERVIEW)
    client.get(MONTHS)
    labels = {tuple(s.labels.values()) for m in metrics.ANALYTICS_REQUESTS.collect() for s in m.samples}
    assert ("overview", "False", "False") in labels and ("months", "False", "False") in labels


@pytest.mark.req("F-181")
def test_a_user_deleted_in_between_gets_a_valid_answer(
    client: TestClient, user: User, add: Any, goal: Any, db: Session
) -> None:
    from basketiq.db.session import get_sessionmaker
    from basketiq.services import analytics

    goal(user, 40_000)
    add(user, datetime(2026, 10, 3, 10, 0), 500)
    assert october(client)["spent_cents"] == 500
    with get_sessionmaker()() as s:  # the account goes away; its receipts and profile go with it (cascade)
        s.execute(delete(User).where(User.id == user.id))
        s.commit()
    with get_sessionmaker()() as s:
        body = analytics.overview(s, user.id, date(2026, 10, 1), TODAY, "Europe/Lisbon", _config())
    assert (body["spent_cents"], body["goal_cents"], body["empty"]) == (0, None, True)
    assert analytics.month_totals(db, user.id, "Europe/Lisbon") == []


def _config() -> AppConfig:
    from basketiq.app_config import DEFAULT_PATH, load_app_config

    return load_app_config(DEFAULT_PATH)


@pytest.mark.req("F-182")
def test_the_overview_reads_a_busy_month_in_a_few_queries(
    client: TestClient, user: User, add: Any, db: Session
) -> None:
    """A guard against a query per receipt: a busy month still costs a handful of statements."""
    from sqlalchemy import event

    from basketiq.db.session import get_engine

    for day in range(1, 29):
        add(user, datetime(2026, 10, day, 12, 0), 100 + day, lines=[(50, 0), (51 + day, 0)])
    statements: list[str] = []

    def count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert october(client)["purchases_this_month"] == 28
    finally:
        event.remove(engine, "before_cursor_execute", count)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 12, selects  # session lookup, consent, then at most the five of the overview
