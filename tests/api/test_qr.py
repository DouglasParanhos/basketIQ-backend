"""The fiscal QR code through the API (spec 009, A1): the duplicate key, what is stored, what never leaves."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from basketiq.db.models import Receipt
from tests.api.flows import import_receipt
from tests.helpers import BUYER, ISSUER, PINGO_LINES, PINGO_TOTAL, at_message, make_pdf_with_qr

FISCAL = at_message(total="8.81", day="20260915")  # the day and total of PINGO_LINES
OTHER_TIME = [line.replace("12:30", "12:31") for line in PINGO_LINES]  # another file, another minute


@pytest.fixture
def user(client: TestClient, login: Any) -> Any:
    return login(client)


def codes(receipt: dict[str, Any]) -> set[str]:
    return {r["code"] for r in receipt["review_reasons"]}


@pytest.mark.req("F-43")
@pytest.mark.req("RF-08")
def test_the_same_fiscal_document_is_a_duplicate_even_when_the_file_and_the_reading_differ(
    client: TestClient, user: Any, storage: Any, run_jobs: Any, login: Any
) -> None:
    first = import_receipt(client, storage, run_jobs, make_pdf_with_qr(PINGO_LINES, FISCAL))
    assert "possible_duplicate" not in codes(first)
    # Another file of the same document: other bytes and another minute, so only the fiscal key sees it.
    second = import_receipt(client, storage, run_jobs, make_pdf_with_qr(OTHER_TIME, FISCAL))
    assert "possible_duplicate" in codes(second)
    # Another document of the same shop (another ATCUD and number) is not.
    another = at_message(
        total="8.81", day="20260915", atcud="JJDBMXMX-052962", number="FS 04360642504231145/052962"
    )
    third = import_receipt(
        client,
        storage,
        run_jobs,
        make_pdf_with_qr([line.replace("12:30", "12:32") for line in PINGO_LINES], another),
    )
    assert "possible_duplicate" not in codes(third)
    # The same document in someone else's account is theirs to import.
    other = TestClient(client.app)
    login(other)
    theirs = import_receipt(other, storage, run_jobs, make_pdf_with_qr(PINGO_LINES, FISCAL))
    assert "possible_duplicate" not in codes(theirs)


@pytest.mark.req("F-43")
def test_what_is_stored_is_only_what_the_checks_use(
    client: TestClient, user: Any, storage: Any, run_jobs: Any, db: Session
) -> None:
    receipt = import_receipt(client, storage, run_jobs, make_pdf_with_qr(PINGO_LINES, FISCAL))
    row = db.scalars(select(Receipt).where(Receipt.id == receipt["id"])).one()
    assert (row.issuer_nif, row.document_type, row.atcud) == (ISSUER, "FS", "JJDBMXMX-052961")
    assert row.document_number == "FS 04360642504231145/052961"
    assert (row.qr_date, row.qr_total_cents, row.qr_tax_cents) == (date(2026, 9, 15), PINGO_TOTAL, 50)
    assert row.qr_notes == [] and row.field_sources == {}
    assert receipt["totals"]["printed_total_cents"] == PINGO_TOTAL and "total_qr_mismatch" not in codes(
        receipt
    )


@pytest.mark.req("F-43")
def test_a_total_that_the_qr_contradicts_is_reviewed_until_the_person_settles_it(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    contradicting = at_message(total="3.17", day="20260915")
    receipt = import_receipt(client, storage, run_jobs, make_pdf_with_qr(PINGO_LINES, contradicting))
    assert receipt["status"] == "REVIEW_REQUIRED" and "total_qr_mismatch" in codes(receipt)
    assert receipt["totals"]["printed_total_cents"] == PINGO_TOTAL  # never lowered in silence
    reason = next(r for r in receipt["review_reasons"] if r["code"] == "total_qr_mismatch")
    assert reason["details"]["read_total_cents"] == PINGO_TOTAL and reason["details"]["qr_total_cents"] == 317
    # Correcting the total settles it: the person has looked.
    edited = client.patch(
        f"/v1/receipts/{receipt['id']}", json={"version": receipt["version"], "total_cents": 1000}
    ).json()
    assert "total_qr_mismatch" not in codes(edited)
    # The reason does not block: a person who sees both values can confirm the receipt as it is.
    other = at_message(total="3.17", day="20260915", atcud="JJDBMXMX-052962", number="FS 1/052962")
    again = import_receipt(client, storage, run_jobs, make_pdf_with_qr(OTHER_TIME, other))
    assert "total_qr_mismatch" in codes(again)
    confirmed = client.post(f"/v1/receipts/{again['id']}/confirm", json={"version": again["version"]}).json()
    assert confirmed["status"] == "CONFIRMED"


@pytest.mark.req("F-44")
def test_the_qr_text_and_the_buyers_number_appear_nowhere(
    client: TestClient,
    user: Any,
    storage: Any,
    run_jobs: Any,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = at_message(total="8.81", day="20260915", extra="S:NU;8.81")
    with caplog.at_level(logging.DEBUG):
        receipt = import_receipt(client, storage, run_jobs, make_pdf_with_qr(PINGO_LINES, message))
        listing = client.get("/v1/receipts")
        one = client.get(f"/v1/receipts/{receipt['id']}")
        analytics = client.get("/v1/analytics/summary?from=2026-09-01&to=2026-09-30")
    assert listing.status_code == one.status_code == analytics.status_code == 200

    rows = []
    for (table,) in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")):
        rows += [r[0] for r in db.execute(text(f'SELECT row_to_json(t)::text FROM "{table}" t'))]  # noqa: S608
    database = "\n".join(rows)
    api = json.dumps(receipt) + listing.text + one.text + analytics.text
    assert ISSUER in database  # the dump did look at the stored receipt
    for where, blob in (("database", database), ("api", api), ("logs", caplog.text)):
        assert BUYER not in blob, where
        assert message not in blob and f"B:{BUYER}" not in blob and "I1:PT*I3" not in blob, where
    # What is stored is not shown either: the API has no field for the issuer, the document or the ATCUD.
    for hidden in ("issuer_nif", "document_number", "atcud", "qr_total_cents", "JJDBMXMX"):
        assert hidden not in api, hidden
