"""Editing a receipt already confirmed (spec 018): it is reopened for review, then confirmed again."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from basketiq.db.models import AuditEvent
from tests.api.flows import import_receipt
from tests.helpers import synthetic

SEPTEMBER = "/v1/analytics/summary?from=2026-09-01&to=2026-09-30"


@pytest.fixture
def confirmed(client: TestClient, login: Any, storage: Any, run_jobs: Any) -> dict[str, Any]:
    """A receipt read, its total fixed by hand and confirmed: it counts 18,17 in September."""
    login(client)
    read = import_receipt(client, storage, run_jobs, synthetic("pingo_doce_mismatch"))
    fixed = client.patch(f"/v1/receipts/{read['id']}", json={"version": read["version"], "total_cents": 1817})
    body = client.post(f"/v1/receipts/{read['id']}/confirm", json={"version": fixed.json()["version"]}).json()
    assert body["status"] == "CONFIRMED"
    return body


def reopen(client: TestClient, receipt: dict[str, Any]) -> Any:
    return client.post(f"/v1/receipts/{receipt['id']}/reopen", json={"version": receipt["version"]})


@pytest.mark.req("F-236")
def test_a_confirmed_receipt_reopens_for_review_and_leaves_the_spending(
    client: TestClient, confirmed: dict[str, Any], db: Any
) -> None:
    resp = reopen(client, confirmed)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"REVIEW_REQUIRED", "COMPLETED"} and body["can_confirm"]
    assert body["confirmed_at"] is None and body["confirmed_by"] is None
    assert body["version"] == confirmed["version"] + 1
    assert client.get(SEPTEMBER).json()["cards"]["purchases"] == 0
    events = db.scalars(select(AuditEvent.event_type).where(AuditEvent.receipt_id == confirmed["id"])).all()
    assert "RECEIPT_REOPENED" in events


@pytest.mark.req("F-237")
def test_a_reopened_receipt_is_edited_and_counts_again_once_confirmed(
    client: TestClient, confirmed: dict[str, Any]
) -> None:
    reopened = reopen(client, confirmed).json()
    first = reopened["lines"][0]
    lines = [{"id": line["id"]} for line in reopened["lines"]]
    lines[0]["discount_cents"] = first["discount_cents"] + 100
    edited = client.patch(
        f"/v1/receipts/{confirmed['id']}",
        json={"version": reopened["version"], "total_cents": 1717, "lines": lines},
    ).json()
    again = client.post(f"/v1/receipts/{confirmed['id']}/confirm", json={"version": edited["version"]}).json()
    assert again["status"] == "CONFIRMED" and again["confirmed_by"] == "USER"
    cards = client.get(SEPTEMBER).json()["cards"]
    assert (cards["purchases"], cards["total_spent_cents"]) == (1, 1717)


@pytest.mark.req("F-238")
def test_reopening_needs_the_current_version_of_a_confirmed_receipt_of_ones_own(
    client: TestClient, confirmed: dict[str, Any], login: Any
) -> None:
    stale = reopen(client, {**confirmed, "version": confirmed["version"] - 1})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    reopened = reopen(client, confirmed).json()
    twice = reopen(client, reopened)
    assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"
    login(client)  # someone else
    assert reopen(client, reopened).status_code == 404
