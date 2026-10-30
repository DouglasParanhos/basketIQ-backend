from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from basketiq.db.models import AuditEvent, ProcessingJob, Receipt
from basketiq.domain.status import JobStatus
from tests.api.flows import import_receipt, process, upload
from tests.helpers import FIXTURES, PINGO_LINES, PINGO_TOTAL, FakeOcr, make_pdf, make_png, synthetic


@pytest.fixture
def user(client: TestClient, login: Any) -> Any:
    return login(client)


# ---------------------------------------------------------------- upload & processing


@pytest.mark.req("F-04")
def test_valid_pdf_creates_a_single_job(
    client: TestClient, user: Any, storage: Any, queue: Any, db: Any
) -> None:
    init = upload(client, storage, make_pdf(PINGO_LINES))
    assert init["requirements"]["max_pdf_pages"] == 5
    r1 = process(client, init["receipt_id"], key="same-key-123")
    r2 = process(client, init["receipt_id"], key="same-key-123")
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 1
    assert len(queue.pending) == 1


@pytest.mark.req("F-15a")
@pytest.mark.req("F-26")
def test_high_confidence_receipt_is_confirmed_automatically(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    assert receipt["status"] == "CONFIRMED" and receipt["confirmed_by"] == "AUTO"
    assert receipt["merchant"] == "PINGO_DOCE"
    assert receipt["totals"]["printed_total_cents"] == PINGO_TOTAL and receipt["totals"]["reconciled"]
    assert receipt["processing"]["route"] == "PDF_TEXT"
    assert receipt["original_expires_at"] is not None
    expires = datetime.fromisoformat(receipt["original_expires_at"])
    confirmed = datetime.fromisoformat(receipt["confirmed_at"])
    assert expires - confirmed == timedelta(days=30)
    assert all(line["product"] for line in receipt["lines"])


@pytest.mark.req("F-05")
@pytest.mark.req("F-27")
@pytest.mark.req("F-55")
def test_image_goes_through_local_ocr_and_waits_for_a_person(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(
        client,
        storage,
        run_jobs,
        make_png(),
        ocr=FakeOcr(PINGO_LINES),
        content_type="image/png",
        filename="foto.png",
    )
    assert receipt["processing"]["route"] == "LOCAL_OCR"
    assert receipt["status"] == "REVIEW_REQUIRED"
    assert [r["code"] for r in receipt["review_reasons"]] == ["photo_not_verified"]
    assert receipt["totals"]["reconciled"] and receipt["can_confirm"]
    body = {"version": receipt["version"]}
    confirmed = client.post(f"/v1/receipts/{receipt['id']}/confirm", json=body).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["confirmed_by"] == "USER"


@pytest.mark.req("F-08")
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"content_type": "application/x-msdownload", "filename": "a.exe"}, "content_type"),
        ({"content_type": "application/pdf", "filename": "a.jpg"}, "filename"),
        ({"size_bytes": 100 * 1024 * 1024}, "size_bytes"),
    ],
)
def test_invalid_type_or_size_rejected_before_upload(
    client: TestClient, user: Any, body: dict[str, Any], field: str
) -> None:
    payload = {
        "filename": "a.pdf",
        "content_type": "application/pdf",
        "size_bytes": 100,
        "sha256": "a" * 64,
        **body,
    }
    resp = client.post("/v1/receipts/upload-init", json=payload)
    assert resp.status_code == 422
    assert field in {e["field"] for e in resp.json()["field_errors"]}


@pytest.mark.req("F-07")
@pytest.mark.req("PT-AC-7")
def test_corrupted_pdf_creates_no_partial_purchase(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, (FIXTURES / "corrupted.pdf").read_bytes())
    assert receipt["status"] == "INVALID_FORMAT"
    assert receipt["error"]["code"] == "pdf_corrupted"
    assert receipt["error"]["action"]  # actionable message
    assert receipt["lines"] == []


@pytest.mark.req("F-08")
def test_fake_extension_is_caught_by_content_sniffing(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, (FIXTURES / "fake.pdf").read_bytes())
    assert (receipt["status"], receipt["error"]["code"]) == ("INVALID_FORMAT", "unsupported_type")


@pytest.mark.req("F-24")
def test_network_loss_during_upload_is_recoverable(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    data = make_pdf(PINGO_LINES)
    init = upload(client, storage, data, put=False)  # upload never reached storage
    resp = process(client, init["receipt_id"])
    assert resp.status_code == 409 and resp.json()["code"] == "upload_missing"
    assert client.get(f"/v1/receipts/{init['receipt_id']}").json()["status"] == "RECEIVED"
    renewed = client.post(f"/v1/receipts/{init['receipt_id']}/upload-url").json()
    storage.put(renewed["fields"]["key"], data, "application/pdf")
    assert process(client, init["receipt_id"]).status_code == 202
    run_jobs()
    assert client.get(f"/v1/receipts/{init['receipt_id']}").json()["status"] == "CONFIRMED"


@pytest.mark.req("F-25")
def test_interrupted_job_is_retried_idempotently(
    client: TestClient, user: Any, storage: Any, run_jobs: Any, db: Any
) -> None:
    init = upload(client, storage, make_png(), content_type="image/png", filename="f.png")
    process(client, init["receipt_id"])
    storage.available = False  # storage outage on the worker side
    run_jobs(ocr=FakeOcr(PINGO_LINES), max_rounds=1)
    job = db.scalar(select(ProcessingJob))
    assert job.status == JobStatus.RETRY and job.attempt_count == 1
    storage.available = True
    run_jobs(ocr=FakeOcr(PINGO_LINES))
    db.expire_all()
    job = db.scalar(select(ProcessingJob))
    assert job.status == JobStatus.SUCCEEDED and job.attempt_count == 2
    receipt = client.get(f"/v1/receipts/{init['receipt_id']}").json()
    assert receipt["status"] == "REVIEW_REQUIRED" and len(receipt["lines"]) == 4  # no duplicated lines


@pytest.mark.req("F-25")
def test_duplicate_queue_message_is_harmless(
    client: TestClient, user: Any, storage: Any, queue: Any, run_jobs: Any, db: Any
) -> None:
    init = upload(client, storage, make_pdf(PINGO_LINES))
    process(client, init["receipt_id"])
    queue.pending.append(queue.pending[0])  # redelivered message
    run_jobs()
    assert db.scalar(select(ProcessingJob)).attempt_count == 1


@pytest.mark.req("F-25")
def test_retries_exhausted_end_in_temporary_failure(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    init = upload(client, storage, make_png(), content_type="image/png", filename="f.png")
    process(client, init["receipt_id"])
    run_jobs(ocr=FakeOcr([], fail=RuntimeError("ocr crashed")))
    receipt = client.get(f"/v1/receipts/{init['receipt_id']}").json()
    assert receipt["status"] == "TEMPORARY_FAILURE"
    assert receipt["error"]["action"].startswith("Ocorreu uma falha temporária")


@pytest.mark.req("F-25")
def test_stuck_job_is_requeued_by_sweeper(
    client: TestClient, user: Any, storage: Any, queue: Any, db: Any
) -> None:
    from basketiq.config import get_settings
    from basketiq.services.processing import requeue_stuck_jobs

    init = upload(client, storage, make_pdf(PINGO_LINES))
    process(client, init["receipt_id"])
    queue.drain()  # message lost
    job = db.scalar(select(ProcessingJob))
    job.status, job.started_at, job.attempt_count = (
        JobStatus.RUNNING,
        datetime.now(UTC) - timedelta(hours=1),
        1,
    )
    db.commit()
    assert requeue_stuck_jobs(db, get_settings(), queue) == 1
    assert queue.drain() == [job.id]


# ---------------------------------------------------------------- review


@pytest.fixture
def review_receipt(client: TestClient, user: Any, storage: Any, run_jobs: Any) -> dict[str, Any]:
    return import_receipt(client, storage, run_jobs, synthetic("pingo_doce_mismatch"))


@pytest.mark.req("F-11")
@pytest.mark.req("F-12")
@pytest.mark.req("F-15b")
def test_low_confidence_receipt_waits_for_review(client: TestClient, review_receipt: dict[str, Any]) -> None:
    assert review_receipt["status"] == "REVIEW_REQUIRED"
    assert {r["code"] for r in review_receipt["review_reasons"]} == {"totals_mismatch"}
    assert review_receipt["can_confirm"]
    assert len(review_receipt["lines"]) == 5
    assert all(line["field_confidence"] for line in review_receipt["lines"])
    # Not in the aggregates while under review.
    summary = client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()
    assert summary["cards"]["purchases"] == 0


@pytest.mark.req("F-13")
@pytest.mark.req("PT-AC-6")
@pytest.mark.req("RF-07")
def test_edit_line_is_marked_manual_and_recalculated(
    client: TestClient, review_receipt: dict[str, Any]
) -> None:
    rid, lines = review_receipt["id"], review_receipt["lines"]
    patch = {
        "version": review_receipt["version"],
        "lines": [{"id": line["id"]} for line in lines],
    }
    patch["lines"][3] = {"id": lines[3]["id"], "quantity": "3"}  # 3 x 2,29
    resp = client.patch(f"/v1/receipts/{rid}", json=patch)
    assert resp.status_code == 200, resp.text
    edited = resp.json()["lines"][3]
    assert edited["line_total_cents"] == 687 and edited["manually_edited"]
    assert set(edited["edited_fields"]) >= {"quantity", "line_total"}
    assert edited["review_status"] == "EDITED"
    # The correction survives a reload.
    again = client.get(f"/v1/receipts/{rid}").json()
    assert again["lines"][3]["quantity"] == "3.000" and again["totals"]["lines_total_cents"] == 1957 + 229


@pytest.mark.req("F-14")
@pytest.mark.req("RF-07")
def test_add_remove_and_split_lines(client: TestClient, review_receipt: dict[str, Any]) -> None:
    rid, lines = review_receipt["id"], review_receipt["lines"]
    keep = [{"id": line["id"]} for line in lines[:-1]]  # remove the banana line (2,07)
    split = [
        {
            "raw_description": "BANANA (parte 1)",
            "quantity": "1",
            "unit": "un",
            "unit_price_cents": 100,
            "line_total_cents": 100,
        },
        {
            "raw_description": "BANANA (parte 2)",
            "quantity": "1",
            "unit": "un",
            "unit_price_cents": 107,
            "line_total_cents": 107,
        },
    ]
    resp = client.patch(
        f"/v1/receipts/{rid}", json={"version": review_receipt["version"], "lines": keep + split}
    )
    body = resp.json()
    assert len(body["lines"]) == 6
    assert body["totals"]["recalculated_total_cents"] == 1817
    assert [line["line_number"] for line in body["lines"]] == [1, 2, 3, 4, 5, 6]


@pytest.mark.req("F-15")
def test_fix_total_then_confirm(client: TestClient, review_receipt: dict[str, Any]) -> None:
    rid = review_receipt["id"]
    body = client.patch(
        f"/v1/receipts/{rid}", json={"version": review_receipt["version"], "total_cents": 1817}
    ).json()
    # The VAT table still says 18,17 → reconciled after the fix.
    assert body["status"] == "COMPLETED" and body["review_reasons"] == []
    confirmed = client.post(f"/v1/receipts/{rid}/confirm", json={"version": body["version"]}).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["confirmed_by"] == "USER"


@pytest.mark.req("SEC-concurrency")
def test_stale_version_is_rejected(client: TestClient, review_receipt: dict[str, Any]) -> None:
    rid, v = review_receipt["id"], review_receipt["version"]
    assert client.patch(f"/v1/receipts/{rid}", json={"version": v, "total_cents": 1817}).status_code == 200
    stale = client.patch(f"/v1/receipts/{rid}", json={"version": v, "total_cents": 1}).json()
    assert stale["code"] == "version_conflict"


@pytest.mark.req("F-15")
def test_confirm_counts_once(client: TestClient, review_receipt: dict[str, Any]) -> None:
    rid, v = review_receipt["id"], review_receipt["version"]
    first = client.post(f"/v1/receipts/{rid}/confirm", json={"version": v})
    second = client.post(f"/v1/receipts/{rid}/confirm", json={"version": v})
    assert first.status_code == second.status_code == 200
    summary = client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()
    assert summary["cards"]["purchases"] == 1
    assert summary["cards"]["total_spent_cents"] == 1817


@pytest.mark.req("PT-7")
def test_pending_quantity_blocks_user_confirmation(
    client: TestClient, review_receipt: dict[str, Any]
) -> None:
    rid, lines = review_receipt["id"], review_receipt["lines"]
    patch_lines = [{"id": line["id"]} for line in lines]
    patch_lines.append({"raw_description": "PRODUTO X", "line_total_cents": 50})
    body = client.patch(
        f"/v1/receipts/{rid}", json={"version": review_receipt["version"], "lines": patch_lines}
    ).json()
    assert not body["can_confirm"]
    resp = client.post(f"/v1/receipts/{rid}/confirm", json={"version": body["version"]})
    assert resp.status_code == 409 and resp.json()["code"] == "review_incomplete"


@pytest.mark.req("RF-10")
def test_product_rename_creates_user_alias(
    client: TestClient, review_receipt: dict[str, Any], login: Any, storage: Any, run_jobs: Any
) -> None:
    rid, lines = review_receipt["id"], review_receipt["lines"]
    patch_lines = [{"id": line["id"]} for line in lines]
    patch_lines[0] = {"id": lines[0]["id"], "product_name": "Arroz Carolino"}
    body = client.patch(
        f"/v1/receipts/{rid}", json={"version": review_receipt["version"], "lines": patch_lines}
    ).json()
    assert body["lines"][0]["product"]["name"] == "Arroz Carolino"
    assert body["lines"][0]["raw_description"] == "ARROZ CAROLINO PD 1KG"  # original preserved
    # Next import of the same description by the same user reuses the correction.
    again = import_receipt(client, storage, run_jobs, synthetic("pingo_doce_text"), allow_duplicate=True)
    assert again["lines"][0]["product"]["name"] == "Arroz Carolino"
    # Another user is not affected.
    other = TestClient(client.app)
    login(other)
    theirs = import_receipt(other, storage, run_jobs, synthetic("pingo_doce_text"))
    assert theirs["lines"][0]["product"]["name"] == "ARROZ CAROLINO PD 1KG"


@pytest.mark.req("F-10")
def test_user_chooses_merchant_or_marks_unsupported(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, synthetic("unknown_merchant"))
    assert "merchant_unknown" in {r["code"] for r in receipt["review_reasons"]}
    resp = client.post(f"/v1/receipts/{receipt['id']}/merchant", json={"merchant": "LIDL"})
    assert resp.status_code == 200
    run_jobs()
    after = client.get(f"/v1/receipts/{receipt['id']}").json()
    assert after["merchant"] == "LIDL"
    marked = client.post(f"/v1/receipts/{receipt['id']}/merchant", json={"merchant": "UNSUPPORTED"}).json()
    assert marked["status"] == "UNSUPPORTED_MERCHANT" and marked["lines"] == []


# ---------------------------------------------------------------- duplicates


@pytest.mark.req("F-17")
@pytest.mark.req("RF-08")
def test_duplicate_detected_before_upload(client: TestClient, user: Any, storage: Any, run_jobs: Any) -> None:
    data = make_pdf(PINGO_LINES)
    import_receipt(client, storage, run_jobs, data)
    resp = client.post(
        "/v1/receipts/upload-init",
        json={
            "filename": "n.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data),
            "sha256": __import__("hashlib").sha256(data).hexdigest(),
        },
    )
    assert resp.status_code == 409 and resp.json()["code"] == "duplicate_suspected"
    assert resp.json()["details"]["duplicate_of"]["total_cents"] == PINGO_TOTAL
    # The user decides to keep a new version.
    kept = import_receipt(client, storage, run_jobs, data, allow_duplicate=True)
    assert kept["status"] == "CONFIRMED" or "possible_duplicate" not in {
        r["code"] for r in kept["review_reasons"]
    }


@pytest.mark.req("F-17")
@pytest.mark.req("RF-08")
def test_attribute_duplicate_requires_decision(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    # Same purchase, different file (e.g. photo vs PDF).
    second = import_receipt(
        client,
        storage,
        run_jobs,
        make_png(),
        ocr=FakeOcr(PINGO_LINES),
        content_type="image/png",
        filename="f.png",
    )
    assert second["status"] == "REVIEW_REQUIRED"
    assert "possible_duplicate" in {r["code"] for r in second["review_reasons"]}
    kept = client.post(f"/v1/receipts/{second['id']}/duplicate-decision", json={"action": "keep"}).json()
    assert "possible_duplicate" not in {r["code"] for r in kept["review_reasons"]}
    third = import_receipt(
        client,
        storage,
        run_jobs,
        make_png(10, 10) if False else make_png(601),
        ocr=FakeOcr(PINGO_LINES),
        content_type="image/png",
        filename="g.png",
    )
    client.post(f"/v1/receipts/{third['id']}/duplicate-decision", json={"action": "discard"})
    assert client.get(f"/v1/receipts/{third['id']}").status_code == 404


# ---------------------------------------------------------------- deletion & retention


@pytest.mark.req("F-22")
@pytest.mark.req("PT-AC-8")
def test_delete_receipt_removes_it_from_aggregates(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    url = "/v1/analytics/summary?from=2026-09-01&to=2026-09-30"
    assert client.get(url).json()["cards"]["purchases"] == 1
    assert client.delete(f"/v1/receipts/{receipt['id']}").status_code == 204
    assert client.get(url).json()["cards"]["purchases"] == 0
    assert storage.objects == {}


@pytest.mark.req("F-22a")
def test_delete_only_original_keeps_structured_data(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    body = client.delete(f"/v1/receipts/{receipt['id']}/original").json()
    assert body["original_deleted_at"] and not body["original_available"]
    assert storage.objects == {}
    assert len(body["lines"]) == 4
    assert client.get(f"/v1/receipts/{receipt['id']}/original").status_code == 410
    assert client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()["cards"]["purchases"] == 1


@pytest.mark.req("F-22b")
def test_retention_job_purges_originals_after_30_days(
    client: TestClient, user: Any, storage: Any, run_jobs: Any, db: Any
) -> None:
    from basketiq.workers.scheduler import purge_expired_originals

    receipt = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    assert purge_expired_originals(db, storage, datetime.now(UTC) + timedelta(days=29)) == 0
    assert purge_expired_originals(db, storage, datetime.now(UTC) + timedelta(days=31)) == 1
    db.commit()
    assert storage.objects == {}
    after = client.get(f"/v1/receipts/{receipt['id']}").json()
    assert after["original_deleted_at"] and len(after["lines"]) == 4
    assert (
        db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ORIGINAL_DELETED")).payload_redacted[
            "reason"
        ]
        == "retention"
    )


@pytest.mark.req("RF-12")
def test_abandoned_uploads_are_cleaned(client: TestClient, user: Any, storage: Any, db: Any) -> None:
    from basketiq.config import get_settings
    from basketiq.workers.scheduler import purge_abandoned_uploads

    upload(client, storage, make_pdf(PINGO_LINES))
    assert purge_abandoned_uploads(db, storage, get_settings(), datetime.now(UTC) + timedelta(hours=25)) == 1
    db.commit()
    assert db.scalar(select(func.count()).select_from(Receipt)) == 0 and storage.objects == {}


@pytest.mark.req("F-23")
def test_account_deletion_removes_everything(
    client: TestClient, user: Any, storage: Any, run_jobs: Any, db: Any
) -> None:
    import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    resp = client.delete("/v1/me")
    assert resp.status_code == 200 and resp.json()["receipts_deleted"] == 1
    assert storage.objects == {}
    assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    assert client.get("/v1/me").status_code == 401
    events = db.scalars(select(AuditEvent)).all()
    assert all(e.user_id is None for e in events)  # audit trail kept, pseudonymised


# ---------------------------------------------------------------- isolation & listing


@pytest.mark.req("PT-AC-9")
@pytest.mark.req("SEC-authz")
def test_user_cannot_read_change_or_delete_another_users_data(
    client: TestClient, user: Any, login: Any, storage: Any, run_jobs: Any
) -> None:
    receipt = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    rid = receipt["id"]
    product_id = receipt["lines"][0]["product"]["id"]
    intruder = TestClient(client.app)
    login(intruder)
    assert intruder.get(f"/v1/receipts/{rid}").status_code == 404
    assert intruder.patch(f"/v1/receipts/{rid}", json={"version": 1, "total_cents": 1}).status_code == 404
    assert intruder.post(f"/v1/receipts/{rid}/confirm", json={"version": 1}).status_code == 404
    assert intruder.delete(f"/v1/receipts/{rid}").status_code == 404
    assert intruder.delete(f"/v1/receipts/{rid}/original").status_code == 404
    assert intruder.get(f"/v1/receipts/{rid}/original").status_code == 404
    assert process(intruder, rid).status_code == 404
    assert intruder.get("/v1/receipts").json()["items"] == []
    assert intruder.get(f"/v1/products/{product_id}/analytics").status_code == 404
    assert (
        intruder.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30").json()["cards"]["purchases"] == 0
    )
    assert client.get(f"/v1/receipts/{rid}").status_code == 200


@pytest.mark.req("SEC-authz")
def test_endpoints_require_authentication(client: TestClient) -> None:
    for method, path in [
        ("get", "/v1/receipts"),
        ("get", "/v1/me"),
        ("get", "/v1/analytics/summary?from=2026-01-01&to=2026-01-31"),
        ("get", "/v1/products"),
        ("get", "/v1/admin/jobs"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, path
        assert set(resp.json()) >= {"code", "message", "request_id"}


@pytest.mark.req("RF-12")
def test_privacy_consent_is_required_for_receipts(client: TestClient, login: Any) -> None:
    login(client, consent=False)
    assert client.get("/v1/receipts").json()["code"] == "privacy_consent_required"
    assert client.post("/v1/me/privacy-consent").status_code == 200
    assert client.get("/v1/receipts").status_code == 200


@pytest.mark.req("RF-12")
def test_list_pagination_and_filters(client: TestClient, user: Any, storage: Any, run_jobs: Any) -> None:
    for name in ("pingo_doce_text", "lidl_text", "unknown_merchant"):
        import_receipt(client, storage, run_jobs, synthetic(name))
    page1 = client.get("/v1/receipts?limit=2").json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = client.get(f"/v1/receipts?limit=2&cursor={page1['next_cursor']}").json()
    assert len(page2["items"]) == 1 and page2["next_cursor"] is None
    ids = {i["id"] for i in page1["items"]} | {i["id"] for i in page2["items"]}
    assert len(ids) == 3
    lidl = client.get("/v1/receipts?merchant=LIDL").json()["items"]
    assert [i["merchant"] for i in lidl] == ["LIDL"]
    review = client.get("/v1/receipts?status=REVIEW_REQUIRED").json()["items"]
    assert [i["merchant"] for i in review] == ["UNKNOWN"]
    assert client.get("/v1/receipts?cursor=garbage").status_code == 422


@pytest.mark.req("SEC-injection")
def test_injection_in_filters_is_inert(client: TestClient, user: Any) -> None:
    assert client.get("/v1/products?q=' OR 1=1 --").status_code == 200
    assert client.get("/v1/products?q=%25").json() == []
    assert client.get("/v1/receipts?merchant=LIDL' OR '1'='1").status_code == 422


@pytest.mark.req("SEC-logs")
def test_errors_do_not_leak_content(client: TestClient, user: Any) -> None:
    resp = client.get("/v1/receipts/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.headers["X-Request-ID"] == resp.json()["request_id"]
