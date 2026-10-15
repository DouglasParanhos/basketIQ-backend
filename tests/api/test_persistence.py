from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from basketiq.auth.sessions import create_session
from basketiq.config import get_settings
from basketiq.db.models import User
from basketiq.db.session import get_sessionmaker
from tests.api.flows import import_receipt
from tests.helpers import synthetic


def _sign_in_again(client: TestClient, user_id: Any) -> None:
    """What a new Google sign-in does for an existing profile: a brand-new session."""
    with get_sessionmaker()() as s:
        user = s.get(User, user_id)
        assert user is not None
        tokens = create_session(s, get_settings(), user)
        s.commit()
    client.cookies.set("biq_at", tokens.access_token)
    client.cookies.set("biq_rt", tokens.refresh_token, path="/v1/auth")
    client.cookies.set("biq_csrf", tokens.csrf_token)
    client.headers["X-CSRF-Token"] = tokens.csrf_token


@pytest.mark.req("F-16")
@pytest.mark.req("PT-AC-6")
def test_confirmed_receipt_with_corrections_survives_logout_and_login(
    client: TestClient, login: Any, storage: Any, run_jobs: Any
) -> None:
    user = login(client)
    receipt = import_receipt(client, storage, run_jobs, synthetic("pingo_doce_mismatch"))
    edited = client.patch(
        f"/v1/receipts/{receipt['id']}", json={"version": receipt["version"], "total_cents": 1817}
    ).json()
    confirmed = client.post(
        f"/v1/receipts/{receipt['id']}/confirm", json={"version": edited["version"]}
    ).json()
    assert confirmed["status"] == "CONFIRMED"

    assert client.post("/v1/auth/logout").status_code == 204
    assert client.get(f"/v1/receipts/{receipt['id']}").status_code == 401

    _sign_in_again(client, user.id)
    again = client.get(f"/v1/receipts/{receipt['id']}").json()
    assert again["status"] == "CONFIRMED" and again["confirmed_by"] == "USER"
    assert again["manually_edited_fields"] == ["total"]
    assert again["totals"]["printed_total_cents"] == 1817
    assert [line["raw_description"] for line in again["lines"]] == [
        line["raw_description"] for line in confirmed["lines"]
    ]
    summary = client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()
    assert summary["cards"]["purchases"] == 1
