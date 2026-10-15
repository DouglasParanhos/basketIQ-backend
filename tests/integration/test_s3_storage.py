"""Object storage against a real S3-compatible server (docker compose `storage` service)."""

from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

from basketiq.config import Settings
from basketiq.storage.s3 import S3Storage

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("BASKETIQ_TEST_S3_ENDPOINT", "http://localhost:9000")


@pytest.fixture(scope="module")
def s3() -> S3Storage:
    try:
        httpx.get(ENDPOINT, timeout=2)
    except httpx.HTTPError:
        pytest.skip(f"S3 endpoint {ENDPOINT} not reachable")
    settings = Settings(
        env="test",
        s3_endpoint_url=ENDPOINT,
        s3_bucket=f"basketiq-it-{uuid.uuid4().hex[:8]}",
        s3_access_key=os.environ.get("BASKETIQ_TEST_S3_ACCESS_KEY", "basketiq"),
        s3_secret_key=os.environ.get("BASKETIQ_TEST_S3_SECRET_KEY", "basketiq-secret"),
    )
    storage = S3Storage(settings)
    storage.ensure_bucket(["http://localhost:5173"])
    return storage


def post(
    upload, data: bytes, content_type: str = "application/pdf", fields: dict | None = None
) -> httpx.Response:  # type: ignore[no-untyped-def]
    return httpx.post(
        upload.url, data={**upload.fields, **(fields or {})}, files={"file": ("x", data, content_type)}
    )


@pytest.mark.req("RF-03")
def test_presigned_post_upload_roundtrip(s3: S3Storage) -> None:
    key = f"users/u/receipts/{uuid.uuid4()}/original"
    up = s3.presign_upload(key, "application/pdf", 1000, 60)
    assert post(up, b"%PDF-1.4 test").status_code in (200, 204)
    assert s3.exists(key) and s3.get(key, 1000) == b"%PDF-1.4 test"
    assert s3.delete_prefix(key.rsplit("/", 1)[0] + "/") == 1
    assert not s3.exists(key)


@pytest.mark.req("F-08")
def test_policy_enforces_size_type_and_key(s3: S3Storage) -> None:
    key = f"users/u/receipts/{uuid.uuid4()}/original"
    up = s3.presign_upload(key, "application/pdf", 100, 60)
    assert post(up, b"x" * 500).status_code == 400  # too large
    assert post(up, b"abc", "image/png", {"Content-Type": "image/png"}).status_code == 403
    assert post(up, b"abc", fields={"key": "users/other/receipts/x/original"}).status_code == 403
    assert not s3.exists(key)


@pytest.mark.req("SEC-urls")
def test_download_urls_expire_and_cannot_be_tampered(s3: S3Storage) -> None:
    key = f"users/u/receipts/{uuid.uuid4()}/original"
    post(s3.presign_upload(key, "application/pdf", 1000, 60), b"%PDF-1.4 secret")
    url = s3.presign_download(key, 1, "application/pdf")
    assert httpx.get(url).status_code == 200
    assert httpx.get(url.replace("/original", "/other")).status_code == 403
    time.sleep(8)  # SeaweedFS tolerates a few seconds of clock skew after X-Amz-Expires
    assert httpx.get(url).status_code == 403
    # No public listing.
    assert httpx.get(f"{ENDPOINT}/{s3.bucket}").status_code == 403
