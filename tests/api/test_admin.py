from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.flows import import_receipt, process, upload
from tests.helpers import FIXTURES, PINGO_LINES, make_pdf


@pytest.fixture
def admin(client: TestClient, login: Any) -> TestClient:
    login(client, role="admin")
    return client


@pytest.mark.req("SEC-authz")
def test_regular_user_cannot_use_admin(client: TestClient, login: Any) -> None:
    login(client)
    assert client.get("/v1/admin/jobs").status_code == 403


@pytest.mark.req("RF-admin")
def test_admin_sees_job_metadata_without_receipt_content(
    admin: TestClient, storage: Any, run_jobs: Any, login: Any
) -> None:
    user = TestClient(admin.app)
    login(user)
    import_receipt(user, storage, run_jobs, make_pdf(PINGO_LINES))
    import_receipt(user, storage, run_jobs, (FIXTURES / "corrupted.pdf").read_bytes())
    jobs = admin.get("/v1/admin/jobs").json()
    assert len(jobs) == 2
    text = str(jobs)
    assert "LEITE" not in text and "@example.test" not in text
    assert {j["processing_route"] for j in jobs} >= {"PDF_TEXT"}
    assert any(j["error_code"] == "pdf_corrupted" for j in jobs)


@pytest.mark.req("RF-admin")
def test_admin_reprocesses_a_failed_job(admin: TestClient, storage: Any, run_jobs: Any, login: Any) -> None:
    user = TestClient(admin.app)
    login(user)
    init = upload(user, storage, make_pdf(PINGO_LINES))
    process(user, init["receipt_id"])
    storage.available = False
    run_jobs()  # exhausts retries → TEMPORARY_FAILURE
    storage.available = True
    failed = admin.get("/v1/admin/jobs?status=FAILED").json()
    assert len(failed) == 1
    resp = admin.post(f"/v1/admin/jobs/{failed[0]['id']}/reprocess")
    assert resp.status_code == 202
    run_jobs()
    assert user.get(f"/v1/receipts/{init['receipt_id']}").json()["status"] == "CONFIRMED"


@pytest.mark.req("RF-admin")
def test_admin_metrics_and_merchant_config(
    admin: TestClient, storage: Any, run_jobs: Any, login: Any
) -> None:
    user = TestClient(admin.app)
    login(user)
    import_receipt(user, storage, run_jobs, make_pdf(PINGO_LINES))
    m = admin.get("/v1/admin/metrics?from=2020-01-01&to=2030-12-31").json()
    assert m["uploads"]["total"] == 1 and m["uploads"]["auto_confirmed"] == 1
    assert m["routes"] == {"PDF_TEXT": 1}
    assert m["without_external_pct"] == 100.0
    assert m["lines_confirmed_without_change_pct"] == 100.0

    configs = admin.get("/v1/admin/merchants").json()
    assert {c["merchant"] for c in configs} == {"CONTINENTE", "PINGO_DOCE", "LIDL", "AUCHAN"}
    updated = admin.put(
        "/v1/admin/merchants/PINGO_DOCE", json={"field_thresholds": {"quantity": 0.95}}
    ).json()
    assert updated["field_thresholds"] == {"quantity": 0.95}
    # The stricter per-field threshold now sends implicit quantities (0.92) to review.
    receipt = import_receipt(user, storage, run_jobs, make_pdf([*PINGO_LINES, "extra"]))
    assert receipt["status"] == "REVIEW_REQUIRED"
    assert "low_confidence" in {r["code"] for r in receipt["review_reasons"]}
    disabled = admin.put("/v1/admin/merchants/PINGO_DOCE", json={"enabled": False}).json()
    assert disabled["enabled"] is False
    receipt = import_receipt(user, storage, run_jobs, make_pdf([*PINGO_LINES, "extra 2"]))
    assert receipt["status"] == "UNSUPPORTED_MERCHANT"
    assert admin.put("/v1/admin/merchants/PINGO_DOCE", json={"default_threshold": 0.1}).status_code == 422
