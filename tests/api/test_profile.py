"""Profile (RF-13, RF-14), merchants list and the consent of the current policy version (spec 013)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from basketiq.db.models import AuditEvent, User, UserProfile
from basketiq.observability.logging import configure_logging
from basketiq.services import accounts
from tests.api.flows import import_receipt
from tests.helpers import PINGO_LINES, make_pdf

PROFILE = "/v1/me/profile"
EMPTY = {
    "household_size": None,
    "merchants": [],
    "goals": [],
    "monthly_goal_cents": None,
    "onboarding_completed_at": None,
}
FULL = {
    "household_size": 2,
    "merchants": ["CONTINENTE", "LIDL", "PINGO_DOCE"],
    "goals": ["finances", "savings"],
    "monthly_goal_cents": 40_000,
}


@pytest.fixture
def user(client: TestClient, login: Any) -> Any:
    return login(client)


# ---------------------------------------------------------------- read and write (US1)


@pytest.mark.req("F-100")
def test_new_account_has_an_empty_profile(client: TestClient, user: Any) -> None:
    resp = client.get(PROFILE)
    assert resp.status_code == 200
    assert resp.json() == EMPTY


@pytest.mark.req("F-101")
def test_profile_is_saved_and_read_back_and_saving_twice_changes_nothing(
    client: TestClient, user: Any
) -> None:
    saved = client.patch(PROFILE, json=FULL)
    assert saved.status_code == 200
    body = saved.json()
    assert (body["household_size"], body["merchants"], body["goals"]) == (2, FULL["merchants"], FULL["goals"])
    assert body["monthly_goal_cents"] == 40_000  # whole cents, not euros
    assert client.get(PROFILE).json() == body
    assert client.patch(PROFILE, json=FULL).json() == body


@pytest.mark.req("F-102")
def test_patch_keeps_what_is_omitted_and_null_clears_a_scalar(client: TestClient, user: Any) -> None:
    client.patch(PROFILE, json=FULL)
    kept = client.patch(PROFILE, json={"household_size": 3}).json()
    assert kept["household_size"] == 3
    assert kept["monthly_goal_cents"] == 40_000
    assert kept["merchants"] == FULL["merchants"]
    assert kept["goals"] == FULL["goals"]

    cleared = client.patch(PROFILE, json={"monthly_goal_cents": None}).json()
    assert cleared["monthly_goal_cents"] is None
    assert cleared["household_size"] == 3

    empty_patch = client.patch(PROFILE, json={})
    assert empty_patch.status_code == 200
    assert empty_patch.json() == cleared

    assert client.patch(PROFILE, json={"merchants": []}).json()["merchants"] == []
    assert client.get(PROFILE).json()["goals"] == FULL["goals"]  # only the list that was sent changed


@pytest.mark.req("PT-AC-9")
@pytest.mark.req("SEC-authz")
def test_profile_is_private_and_needs_a_session(client: TestClient, user: Any, login: Any) -> None:
    client.patch(PROFILE, json=FULL)
    intruder = TestClient(client.app)
    login(intruder)
    assert intruder.get(PROFILE).json() == EMPTY
    assert intruder.patch(PROFILE, json={"household_size": 5}).json()["household_size"] == 5
    assert client.get(PROFILE).json()["household_size"] == 2  # the other account is untouched

    anonymous = TestClient(client.app)
    assert anonymous.get(PROFILE).status_code == 401
    assert anonymous.patch(PROFILE, json={}).status_code == 403  # no CSRF token: refused before the session
    anonymous.cookies.set("biq_csrf", "token")
    anonymous.headers["X-CSRF-Token"] = "token"
    assert anonymous.patch(PROFILE, json={}).status_code == 401


# ---------------------------------------------------------------- validation (US2)


def _refused(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.patch(PROFILE, json=body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"
    return resp.json()


@pytest.mark.req("F-103")
@pytest.mark.parametrize(("size", "accepted"), [(0, False), (1, True), (12, True), (13, False)])
def test_household_size_goes_from_1_to_12(client: TestClient, user: Any, size: int, accepted: bool) -> None:
    resp = client.patch(PROFILE, json={"household_size": size})
    assert resp.status_code == (200 if accepted else 422)
    assert client.get(PROFILE).json()["household_size"] == (size if accepted else None)


@pytest.mark.req("F-103")
@pytest.mark.parametrize(
    ("cents", "accepted"), [(4_999, False), (5_000, True), (80_000, True), (80_001, False), (None, True)]
)
def test_monthly_goal_goes_from_50_to_800_euros_or_is_cleared(
    client: TestClient, user: Any, cents: int | None, accepted: bool
) -> None:
    resp = client.patch(PROFILE, json={"monthly_goal_cents": cents})
    assert resp.status_code == (200 if accepted else 422)


@pytest.mark.req("F-103")
@pytest.mark.parametrize("value", [400.5, 400.0, "40000", True])
def test_monthly_goal_is_whole_cents_never_a_decimal_or_text(
    client: TestClient, user: Any, value: Any
) -> None:
    _refused(client, {"monthly_goal_cents": value})
    assert client.get(PROFILE).json()["monthly_goal_cents"] is None


@pytest.mark.req("F-103")
@pytest.mark.parametrize("value", [2.5, "2", True])
def test_household_size_is_a_whole_number(client: TestClient, user: Any, value: Any) -> None:
    _refused(client, {"household_size": value})


@pytest.mark.req("F-103")
def test_merchants_must_be_supported_and_repeats_count_once(client: TestClient, user: Any) -> None:
    for bad in (["NAO_EXISTE"], ["LIDL", "UNKNOWN"], ["lidl"]):
        _refused(client, {"merchants": bad})
    saved = client.patch(PROFILE, json={"merchants": ["LIDL", "ALDI", "LIDL"]}).json()
    assert saved["merchants"] == ["LIDL", "ALDI"]


@pytest.mark.req("F-103")
def test_goals_must_be_one_of_the_three_and_repeats_count_once(client: TestClient, user: Any) -> None:
    for bad in (["all"], ["finances", "other"]):
        _refused(client, {"goals": bad})
    saved = client.patch(PROFILE, json={"goals": ["savings", "finances", "savings"]}).json()
    assert saved["goals"] == ["savings", "finances"]


@pytest.mark.req("F-103")
def test_lists_are_never_null_and_an_empty_list_clears_them(client: TestClient, user: Any) -> None:
    client.patch(PROFILE, json=FULL)
    _refused(client, {"merchants": None})
    _refused(client, {"goals": None})
    assert client.get(PROFILE).json()["merchants"] == FULL["merchants"]
    cleared = client.patch(PROFILE, json={"merchants": [], "goals": []}).json()
    assert (cleared["merchants"], cleared["goals"]) == ([], [])


@pytest.mark.req("F-103")
def test_a_refused_request_stores_nothing_not_even_its_valid_fields(client: TestClient, user: Any) -> None:
    body = _refused(client, {"household_size": 3, "merchants": ["LIDL"], "monthly_goal_cents": 1})
    assert client.get(PROFILE).json() == EMPTY
    errors = {e["field"]: e["message"] for e in body["field_errors"]}
    assert list(errors) == ["monthly_goal_cents"]
    assert "50" in errors["monthly_goal_cents"] and "800" in errors["monthly_goal_cents"]  # says the limit
    assert "1 e 12" in _refused(client, {"household_size": 13})["field_errors"][0]["message"]


@pytest.mark.req("F-103")
def test_an_unknown_field_is_refused_instead_of_silently_ignored(client: TestClient, user: Any) -> None:
    _refused(client, {"monthly_goal": 40_000})


# ---------------------------------------------------------------- onboarding completed (US3)


@pytest.mark.req("F-104")
def test_completing_the_onboarding_stores_the_date_and_no_value(client: TestClient, user: Any) -> None:
    first = client.patch(PROFILE, json={"onboarding_completed": True}).json()
    assert first["onboarding_completed_at"] is not None
    assert first | {"onboarding_completed_at": None} == EMPTY  # every step skipped: nothing else is saved


@pytest.mark.req("F-104")
def test_completing_again_keeps_the_first_date_and_the_data(client: TestClient, user: Any) -> None:
    first = client.patch(PROFILE, json={"onboarding_completed": True}).json()
    again = client.patch(PROFILE, json={"onboarding_completed": True, "household_size": 3}).json()
    assert again["onboarding_completed_at"] == first["onboarding_completed_at"]
    assert again["household_size"] == 3
    assert (
        client.patch(PROFILE, json={"household_size": 4}).json()["onboarding_completed_at"]
        == first["onboarding_completed_at"]
    )


@pytest.mark.req("F-104")
@pytest.mark.parametrize("value", [False, None])
def test_the_onboarding_cannot_be_undone(client: TestClient, user: Any, value: Any) -> None:
    done = client.patch(PROFILE, json={"onboarding_completed": True}).json()
    _refused(client, {"onboarding_completed": value})
    assert client.get(PROFILE).json()["onboarding_completed_at"] == done["onboarding_completed_at"]


@pytest.mark.req("F-104")
def test_an_account_with_receipts_and_no_profile_has_not_completed_it(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    assert client.get(PROFILE).json() == EMPTY


# ---------------------------------------------------------------- merchants (US4)

SUPPORTED_IDS = ["CONTINENTE", "PINGO_DOCE", "LIDL", "AUCHAN", "ALDI", "MERCADONA", "EL_CORTE_INGLES"]


@pytest.mark.req("F-105")
def test_merchants_lists_the_seven_supported_with_the_name_to_show(client: TestClient, user: Any) -> None:
    resp = client.get("/v1/merchants")
    assert resp.status_code == 200
    items = resp.json()
    assert [m["id"] for m in items] == SUPPORTED_IDS  # fixed order, never «unknown»
    assert {m["id"]: m["name"] for m in items}["EL_CORTE_INGLES"] == "El Corte Inglés"
    assert all(set(m) == {"id", "name"} and m["name"] for m in items)  # no parsing parameters


@pytest.mark.req("F-105")
def test_merchants_needs_a_session_but_not_the_operator_role(client: TestClient, user: Any) -> None:
    assert user.role == "user"
    assert client.get("/v1/merchants").status_code == 200
    assert TestClient(client.app).get("/v1/merchants").status_code == 401


@pytest.mark.req("F-105")
def test_a_profile_keeps_reading_when_a_merchant_stops_being_supported(
    client: TestClient, user: Any, db: Any
) -> None:
    db.add(UserProfile(user_id=user.id, merchants=["LOJA_QUE_JA_NAO_EXISTE", "LIDL"]))
    db.commit()
    resp = client.get(PROFILE)
    assert resp.status_code == 200
    assert resp.json()["merchants"] == ["LOJA_QUE_JA_NAO_EXISTE", "LIDL"]


# ---------------------------------------------------------------- consent of the current policy version (US5)

NEW_VERSION = "2026-12-01"
GATED = [
    ("PATCH", PROFILE, {"household_size": 3}),
    ("GET", "/v1/receipts", None),
    ("GET", "/v1/analytics/summary?from=2026-09-01&to=2026-09-30", None),
]


@pytest.fixture
def new_policy(monkeypatch: pytest.MonkeyPatch) -> str:
    """The policy changes after the person consented: only the version in the code moves, as at release."""

    monkeypatch.setattr(accounts, "PRIVACY_POLICY_VERSION", NEW_VERSION)
    return NEW_VERSION


@pytest.mark.req("F-106")
def test_a_new_policy_version_leaves_the_consent_pending(
    client: TestClient, user: Any, new_policy: str
) -> None:
    me = client.get("/v1/me").json()
    assert me["current_privacy_version"] == new_policy
    assert me["privacy_version"] == user.privacy_version  # what the person accepted, not the new version
    assert me["privacy_version"] != me["current_privacy_version"]


@pytest.mark.req("F-106")
@pytest.mark.parametrize(("method", "path", "body"), GATED)
def test_every_endpoint_that_needs_consent_refuses_a_stale_one(
    client: TestClient, user: Any, new_policy: str, method: str, path: str, body: Any
) -> None:
    resp = client.request(method, path, json=body)
    assert resp.status_code == 403
    assert resp.json()["code"] == "privacy_consent_required"
    assert client.get(PROFILE).json() == EMPTY  # nothing was saved by the refused request


@pytest.mark.req("F-106")
def test_the_person_can_still_consent_read_the_profile_and_see_the_account(
    client: TestClient, user: Any, new_policy: str
) -> None:
    assert client.get("/v1/me").status_code == 200
    assert client.get(PROFILE).status_code == 200
    resp = client.post("/v1/me/privacy-consent")
    assert resp.status_code == 200
    assert resp.json()["privacy_version"] == new_policy


@pytest.mark.req("F-106")
def test_after_consenting_everything_is_accepted_again(
    client: TestClient, user: Any, new_policy: str
) -> None:
    assert client.patch(PROFILE, json={"household_size": 3}).status_code == 403
    client.post("/v1/me/privacy-consent")
    assert client.patch(PROFILE, json={"household_size": 3}).json()["household_size"] == 3
    assert client.get("/v1/receipts").status_code == 200
    assert client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").status_code == 200


@pytest.mark.req("F-106")
def test_the_person_can_still_delete_the_account_with_a_stale_consent(
    client: TestClient, user: Any, new_policy: str
) -> None:
    assert client.delete("/v1/me").status_code == 200


@pytest.mark.req("F-106")
def test_a_consent_without_a_recorded_version_counts_as_pending(
    client: TestClient, login: Any, db: Any
) -> None:
    person = login(client)
    db.execute(update(User).where(User.id == person.id).values(privacy_version=None))
    db.commit()
    assert client.get("/v1/me").json()["privacy_accepted_at"] is not None
    resp = client.get("/v1/receipts")
    assert resp.status_code == 403
    assert resp.json()["code"] == "privacy_consent_required"
    client.post("/v1/me/privacy-consent")
    assert client.get("/v1/receipts").status_code == 200


@pytest.mark.req("F-106")
def test_the_consent_is_audited_without_personal_data(
    client: TestClient, user: Any, new_policy: str, db: Any
) -> None:
    client.post("/v1/me/privacy-consent")
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "PRIVACY_ACCEPTED")).one()
    assert event.payload_redacted.get("version") == new_policy
    assert user.email not in str(event.payload_redacted)


# ---------------------------------------------------------------- privacy (constitution IV)


@pytest.mark.req("SEC-logs")
def test_profile_values_reach_neither_the_logs_nor_the_audit_trail(
    client: TestClient, user: Any, db: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    saved = client.patch(
        PROFILE,
        json={"household_size": 7, "monthly_goal_cents": 43_210, "merchants": ["ALDI"], "goals": ["savings"]},
    )
    assert saved.status_code == 200
    assert client.patch(PROFILE, json={"merchants": ["LOJA_SECRETA_XYZ"]}).status_code == 422
    client.get(PROFILE)

    logs = capsys.readouterr().out
    assert "request" in logs  # the requests were logged, only without their content
    for private in ("43210", "LOJA_SECRETA_XYZ", "monthly_goal", "household_size"):
        assert private not in logs
    audit = " ".join(str(e.payload_redacted) for e in db.scalars(select(AuditEvent)).all())
    assert "43210" not in audit and "ALDI" not in audit  # saving the profile writes no audit event
