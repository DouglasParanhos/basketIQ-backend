"""One long receipt photographed in several pieces: the photos are joined before the receipt is read."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from basketiq.pipeline.schema import TextToken
from tests.api.flows import process, upload
from tests.helpers import StubOcr, make_png, sha256

FIRST = ["LIDL & Cia", "NIF:503340855", "EUR", "LEITE 0,79 B", "PAO 1,29 B", "ARROZ 1,10 B", "OVOS 2,50 B"]
SECOND = ["ARROZ 1,10 B", "OVOS 2,50 B", "SUMO 1,99 B", "Total 7,67", "10.10.26 13:45"]


class OneReadingPerPhoto(StubOcr):
    """An OCR that reads a different set of lines for every photo it is handed, in order."""

    name = "per-photo-ocr"

    def __init__(self, *photos: list[str]) -> None:
        self.photos = list(photos)
        self.calls = 0

    def recognize(self, image: Image.Image, page: int = 0) -> list[TextToken]:
        lines = self.photos[self.calls]
        self.calls += 1
        return [
            TextToken(text=t, x0=0, y0=i * 20, x1=300, y1=i * 20 + 15, confidence=0.97, page=page)
            for i, t in enumerate(lines)
        ]


@pytest.fixture
def user(client: TestClient, login: Any) -> Any:
    return login(client)


def add_photo(client: TestClient, storage: Any, receipt_id: str, data: bytes, *, put: bool = True) -> Any:
    resp = client.post(
        f"/v1/receipts/{receipt_id}/photos",
        json={
            "filename": "foto.png",
            "content_type": "image/png",
            "size_bytes": len(data),
            "sha256": sha256(data),
        },
    )
    if resp.status_code == 201 and put:
        storage.put(resp.json()["fields"]["key"], data, "image/png")
    return resp


def first_photo(client: TestClient, storage: Any, width: int = 600) -> str:
    return upload(client, storage, make_png(width, 900), content_type="image/png", filename="a.png")[
        "receipt_id"
    ]


@pytest.mark.req("RF-03")
def test_two_photos_become_one_receipt_with_the_repeated_lines_removed(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt_id = first_photo(client, storage)
    assert add_photo(client, storage, receipt_id, make_png(601, 900)).status_code == 201
    assert process(client, receipt_id).status_code == 202
    ocr = OneReadingPerPhoto(FIRST, SECOND)
    run_jobs(ocr=ocr)

    receipt = client.get(f"/v1/receipts/{receipt_id}").json()
    assert ocr.calls == 2
    assert [line["raw_description"] for line in receipt["lines"]] == ["LEITE", "PAO", "ARROZ", "OVOS", "SUMO"]
    assert receipt["totals"]["printed_total_cents"] == 767 and receipt["totals"]["reconciled"]
    assert receipt["photo_count"] == 2
    joined = next(r for r in receipt["review_reasons"] if r["code"] == "photos_joined")
    assert "ARROZ 1,10 B" in joined["message"] and joined["details"]["overlap"] == 2
    assert receipt["status"] == "REVIEW_REQUIRED"


@pytest.mark.req("RF-03")
def test_photos_without_lines_in_common_are_joined_and_flagged(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt_id = first_photo(client, storage)
    add_photo(client, storage, receipt_id, make_png(601, 900))
    process(client, receipt_id)
    run_jobs(ocr=OneReadingPerPhoto(FIRST, ["SUMO 1,99 B", "CAFE 3,20 B", "Total 7,67", "10.10.26 13:45"]))
    receipt = client.get(f"/v1/receipts/{receipt_id}").json()
    assert len(receipt["lines"]) == 6
    assert "photos_no_overlap" in {r["code"] for r in receipt["review_reasons"]}


@pytest.mark.req("RF-03")
def test_the_review_still_reports_the_join_after_an_edit(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt_id = first_photo(client, storage)
    add_photo(client, storage, receipt_id, make_png(601, 900))
    process(client, receipt_id)
    run_jobs(ocr=OneReadingPerPhoto(FIRST, SECOND))
    receipt = client.get(f"/v1/receipts/{receipt_id}").json()
    edited = client.patch(
        f"/v1/receipts/{receipt_id}",
        json={"version": receipt["version"], "lines": [{"id": receipt["lines"][0]["id"], "quantity": "1"}]},
    ).json()
    assert "photos_joined" in {r["code"] for r in edited["review_reasons"]}


@pytest.mark.req("F-24")
def test_processing_waits_for_every_photo_to_be_uploaded(client: TestClient, user: Any, storage: Any) -> None:
    receipt_id = first_photo(client, storage)
    add_photo(client, storage, receipt_id, make_png(601, 900), put=False)
    resp = process(client, receipt_id)
    assert resp.status_code == 409 and resp.json()["code"] == "upload_missing"


@pytest.mark.req("RF-03")
def test_photo_limit_type_and_state_are_enforced(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt_id = first_photo(client, storage)
    for i in range(5):  # six photos in all
        assert add_photo(client, storage, receipt_id, make_png(601 + i, 900)).status_code == 201
    assert add_photo(client, storage, receipt_id, make_png(700, 900)).status_code == 422

    other = first_photo(client, storage, 650)  # a different receipt: not a photo, then after processing
    pdf = client.post(
        f"/v1/receipts/{other}/photos",
        json={"filename": "a.pdf", "content_type": "application/pdf", "size_bytes": 10, "sha256": "0" * 64},
    )
    assert pdf.status_code == 422
    process(client, other)
    assert add_photo(client, storage, other, make_png(601, 900)).status_code == 409


@pytest.mark.req("RF-03")
def test_each_photo_can_be_opened_by_number(
    client: TestClient, user: Any, storage: Any, run_jobs: Any
) -> None:
    receipt_id = first_photo(client, storage)
    add_photo(client, storage, receipt_id, make_png(601, 900))
    process(client, receipt_id)
    run_jobs(ocr=OneReadingPerPhoto(FIRST, SECOND))
    assert client.get(f"/v1/receipts/{receipt_id}/original?photo=1").status_code == 200
    assert client.get(f"/v1/receipts/{receipt_id}/original?photo=2").status_code == 200
    assert client.get(f"/v1/receipts/{receipt_id}/original?photo=3").status_code == 404
