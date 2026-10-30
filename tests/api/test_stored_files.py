"""See and delete every stored original at once (RF-12, spec 013 US7). Test data is synthetic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from basketiq.db.models import AuditEvent, Receipt, ReceiptPhoto
from basketiq.domain.status import ReceiptStatus
from basketiq.services import receipts as receipts_service
from basketiq.storage import original_key, photo_key
from tests.api.flows import import_receipt
from tests.helpers import PINGO_LINES, make_pdf

URL = "/v1/receipts/stored-files"


@pytest.fixture
def user(client: TestClient, login: Any) -> Any:
    return login(client)


def make_receipt(
    db: Any,
    storage: Any,
    owner: Any,
    *,
    photos: int = 0,
    status: ReceiptStatus = ReceiptStatus.CONFIRMED,
    deleted: bool = False,
) -> Receipt:
    """A receipt with its original (and `photos` extra photos) in the storage."""
    rid = uuid.uuid4()
    receipt = Receipt(
        id=rid,
        user_id=owner.id,
        source_type="image",
        content_type="image/png",
        size_bytes=1,
        object_key=original_key(owner.id, rid),
        processing_status=status,
        original_deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(receipt)
    db.flush()
    if not deleted:
        storage.put(receipt.object_key, b"x", "image/png")
    for position in range(1, photos + 1):
        key = photo_key(owner.id, rid, position)
        db.add(
            ReceiptPhoto(
                receipt_id=rid, position=position, object_key=key, content_type="image/png", size_bytes=1
            )
        )
        if not deleted:
            storage.put(key, b"x", "image/png")
    db.commit()
    return receipt


# ---------------------------------------------------------------- summary


@pytest.mark.req("F-108")
def test_summary_is_zero_without_originals(client: TestClient, user: Any) -> None:
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"files": 0}


@pytest.mark.req("F-108")
def test_summary_counts_every_file_of_the_confirmed_receipts(
    client: TestClient, user: Any, db: Any, storage: Any
) -> None:
    make_receipt(db, storage, user)  # one file
    make_receipt(db, storage, user, photos=2)  # the first photo plus two more
    assert client.get(URL).json() == {"files": 4}


@pytest.mark.req("F-108")
def test_summary_leaves_out_unconfirmed_deleted_and_other_peoples_files(
    client: TestClient, user: Any, db: Any, storage: Any, login: Any
) -> None:
    make_receipt(db, storage, user, photos=1)  # 2 files, the only ones counted
    make_receipt(db, storage, user, status=ReceiptStatus.REVIEW_REQUIRED, photos=3)
    make_receipt(db, storage, user, deleted=True, photos=2)
    other = TestClient(client.app)
    stranger = login(other)
    make_receipt(db, storage, stranger, photos=4)
    assert client.get(URL).json() == {"files": 2}
    assert other.get(URL).json() == {"files": 5}


# ---------------------------------------------------------------- delete all


@pytest.mark.req("F-109")
def test_delete_all_removes_the_files_and_keeps_the_purchases_and_their_data(
    client: TestClient, user: Any, db: Any, storage: Any, run_jobs: Any
) -> None:
    imported = import_receipt(client, storage, run_jobs, make_pdf(PINGO_LINES))
    make_receipt(db, storage, user, photos=2)
    files_before = len(storage.objects)
    assert files_before == 4  # the imported original and three files of the synthetic receipt

    resp = client.delete(URL)
    assert resp.status_code == 200
    assert resp.json() == {"files_deleted": 4, "receipts": 2, "remaining": 0}
    assert storage.objects == {}
    assert client.get(URL).json() == {"files": 0}

    after = client.get(f"/v1/receipts/{imported['id']}").json()
    assert after["original_deleted_at"] and len(after["lines"]) == 4  # purchase and lines are still there
    assert len(db.scalars(select(Receipt)).all()) == 2
    events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "ORIGINAL_DELETED")).all()
    assert len(events) == 2
    assert {e.payload_redacted["reason"] for e in events} == {"user_bulk_request"}


@pytest.mark.req("F-109")
def test_delete_all_is_idempotent(client: TestClient, user: Any, db: Any, storage: Any) -> None:
    assert client.delete(URL).json() == {"files_deleted": 0, "receipts": 0, "remaining": 0}
    make_receipt(db, storage, user, photos=1)
    assert client.delete(URL).json()["files_deleted"] == 2
    assert client.delete(URL).json() == {"files_deleted": 0, "receipts": 0, "remaining": 0}


@pytest.mark.req("F-109")
def test_delete_all_does_not_touch_unconfirmed_receipts_or_other_accounts(
    client: TestClient, user: Any, db: Any, storage: Any, login: Any
) -> None:
    pending = make_receipt(db, storage, user, status=ReceiptStatus.REVIEW_REQUIRED, photos=1)
    other = TestClient(client.app)
    stranger = login(other)
    theirs = make_receipt(db, storage, stranger, photos=1)
    make_receipt(db, storage, user)

    assert client.delete(URL).json()["receipts"] == 1
    assert pending.object_key in storage.objects  # still reviewable and reprocessable
    assert theirs.object_key in storage.objects
    db.expire_all()
    assert db.get(Receipt, pending.id).original_deleted_at is None
    assert db.get(Receipt, theirs.id).original_deleted_at is None


@pytest.mark.req("F-109")
def test_delete_all_is_limited_to_500_receipts_a_call_and_reports_what_remains(
    client: TestClient, user: Any, db: Any, storage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert receipts_service.STORED_FILES_BATCH == 500
    monkeypatch.setattr(receipts_service, "STORED_FILES_BATCH", 3)
    for _ in range(7):
        make_receipt(db, storage, user)
    results = [client.delete(URL).json() for _ in range(3)]
    assert [(r["receipts"], r["remaining"]) for r in results] == [(3, 4), (3, 1), (1, 0)]
    assert sum(r["files_deleted"] for r in results) == 7
    assert storage.objects == {}


# ---------------------------------------------------------------- routes and access


@pytest.mark.req("F-110")
def test_the_routes_are_not_taken_for_a_receipt_id_nor_for_the_single_original(
    client: TestClient, user: Any, db: Any, storage: Any
) -> None:
    first = make_receipt(db, storage, user)
    make_receipt(db, storage, user, photos=1)
    assert client.get(URL).status_code == 200  # would be 422 if `/{receipt_id}` took «stored-files»
    assert client.get(URL).json() == {"files": 3}
    single = client.delete(f"/v1/receipts/{first.id}/original")  # the one-by-one route is unchanged
    assert single.status_code == 200
    assert client.get(URL).json() == {"files": 2}
    assert client.delete(URL).status_code == 200


@pytest.mark.req("F-110")
def test_the_routes_need_a_session_and_the_consent_of_the_current_policy(
    client: TestClient, login: Any
) -> None:
    anonymous = TestClient(client.app)
    assert anonymous.get(URL).status_code == 401
    pending = TestClient(client.app)
    login(pending, consent=False)
    for resp in (pending.get(URL), pending.delete(URL)):
        assert resp.status_code == 403
        assert resp.json()["code"] == "privacy_consent_required"
