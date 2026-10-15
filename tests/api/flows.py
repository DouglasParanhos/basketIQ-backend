"""Helpers that drive the API like the frontend does."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import sha256


def upload(
    client: TestClient,
    storage: Any,
    data: bytes,
    *,
    content_type: str = "application/pdf",
    filename: str = "nota.pdf",
    merchant_hint: str | None = None,
    allow_duplicate: bool = False,
    put: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": sha256(data),
        "allow_duplicate": allow_duplicate,
    }
    if merchant_hint:
        body["merchant_hint"] = merchant_hint
    resp = client.post("/v1/receipts/upload-init", json=body)
    assert resp.status_code == 201, resp.text
    init = resp.json()
    if put:  # what the browser does with the presigned POST
        storage.put(init["upload"]["fields"]["key"], data, content_type)
    return init


def process(client: TestClient, receipt_id: str, key: str | None = None, **body: Any) -> Any:
    return client.post(
        f"/v1/receipts/{receipt_id}/process",
        headers={"Idempotency-Key": key or f"key-{uuid.uuid4()}"},
        json=body or None,
    )


def import_receipt(
    client: TestClient, storage: Any, run_jobs: Any, data: bytes, ocr: Any = None, **kw: Any
) -> dict[str, Any]:
    init = upload(client, storage, data, **kw)
    assert process(client, init["receipt_id"]).status_code == 202
    run_jobs(ocr=ocr)
    resp = client.get(f"/v1/receipts/{init['receipt_id']}")
    assert resp.status_code == 200, resp.text
    return resp.json()
