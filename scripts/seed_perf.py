"""Seed an account with N confirmed receipt lines and benchmark the dashboard (spec técnica §9).

    python scripts/seed_perf.py --lines 5000 --bench
    python scripts/seed_perf.py --lines 50000 --bench

Uses BASKETIQ_DATABASE_URL (default: local dev database). Data is synthetic.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

os.environ.setdefault("BASKETIQ_STORAGE_BACKEND", "memory")
os.environ.setdefault("BASKETIQ_QUEUE_BACKEND", "inline")
os.environ.setdefault("BASKETIQ_RATE_LIMIT_ENABLED", "false")

from sqlalchemy import insert  # noqa: E402

from basketiq.auth.sessions import create_session  # noqa: E402
from basketiq.config import get_settings  # noqa: E402
from basketiq.db.models import Product, Receipt, ReceiptLine, User  # noqa: E402
from basketiq.db.session import get_sessionmaker  # noqa: E402

MERCHANTS = ["PINGO_DOCE", "LIDL", "CONTINENTE", "AUCHAN"]
PRODUCTS = [f"PRODUTO SINTETICO {i:03d}" for i in range(300)]


def seed(lines: int, seed_value: int = 7) -> tuple[uuid.UUID, str, str]:
    rnd = random.Random(seed_value)
    settings = get_settings()
    with get_sessionmaker()() as db:
        user = User(
            google_subject=f"perf|{uuid.uuid4()}",
            email=f"perf-{uuid.uuid4().hex[:6]}@example.test",
            privacy_accepted_at=datetime.now(UTC),
            privacy_version="2026-10-15",
        )
        db.add(user)
        db.flush()
        product_ids = []
        for name in PRODUCTS:
            p = Product(canonical_name=name, normalized_key=f"{name} {uuid.uuid4().hex[:6]}", unit_type="un", owner_user_id=user.id)
            db.add(p)
            db.flush()
            product_ids.append(p.id)
        receipts, line_rows = [], []
        remaining = lines
        start = datetime(2025, 10, 1, tzinfo=UTC)
        while remaining > 0:
            n = min(remaining, rnd.randint(5, 25))
            rid = uuid.uuid4()
            purchase_at = start + timedelta(minutes=rnd.randint(0, 365 * 24 * 60))
            total = 0
            for i in range(n):
                qty = Decimal(rnd.randint(1, 3))
                price = rnd.randint(30, 900)
                line_total = int(qty) * price
                total += line_total
                line_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "receipt_id": rid,
                        "line_number": i + 1,
                        "raw_description": PRODUCTS[i % len(PRODUCTS)],
                        "normalized_product_id": rnd.choice(product_ids),
                        "quantity_decimal": qty,
                        "unit": "un",
                        "unit_price_cents": price,
                        "line_total_cents": line_total,
                        "discount_cents": 0,
                        "confidence": 0.95,
                        "field_confidence": {},
                        "evidence": {},
                        "review_status": "OK",
                        "manually_edited": False,
                        "edited_fields": [],
                    }
                )
            receipts.append(
                {
                    "id": rid,
                    "user_id": user.id,
                    "merchant": rnd.choice(MERCHANTS),
                    "purchase_at": purchase_at,
                    "currency": "EUR",
                    "source_type": "PDF",
                    "content_type": "application/pdf",
                    "size_bytes": 1000,
                    "object_key": f"users/{user.id}/receipts/{rid}/original",
                    "processing_status": "CONFIRMED",
                    "total_cents": total,
                    "field_confidence": {},
                    "review_reasons": [],
                    "quality_warnings": [],
                    "manually_edited_fields": [],
                    "duplicate_acknowledged": False,
                    "confirmed_at": purchase_at,
                    "confirmed_by": "AUTO",
                    "version": 1,
                }
            )
            remaining -= n
        db.execute(insert(Receipt), receipts)
        for chunk in range(0, len(line_rows), 5000):
            db.execute(insert(ReceiptLine), line_rows[chunk : chunk + 5000])
        tokens = create_session(db, settings, user)
        db.commit()
        print(f"seeded user {user.id}: {len(receipts)} receipts, {len(line_rows)} lines")
        return user.id, tokens.access_token, tokens.csrf_token


def bench(access_token: str, runs: int = 30) -> None:
    from fastapi.testclient import TestClient

    from basketiq.api.main import app

    client = TestClient(app)
    client.cookies.set("biq_at", access_token)
    cases = {
        "month": "/v1/analytics/summary?from=2026-03-01&to=2026-03-31",
        "year": "/v1/analytics/summary?from=2025-10-01&to=2026-09-30",
        "year+merchant": "/v1/analytics/summary?from=2025-10-01&to=2026-09-30&merchant=LIDL",
    }
    for name, url in cases.items():
        timings = []
        for _ in range(runs):
            t = time.perf_counter()
            resp = client.get(url)
            timings.append((time.perf_counter() - t) * 1000)
            assert resp.status_code == 200, resp.text
        timings.sort()
        p95 = timings[int(len(timings) * 0.95) - 1]
        print(f"{name:14} p50={statistics.median(timings):7.1f} ms  p95={p95:7.1f} ms  p99={timings[-1]:7.1f} ms")
    product = client.get("/v1/products?limit=1").json()[0]["id"]
    t = time.perf_counter()
    client.get(f"/v1/products/{product}/analytics")
    print(f"product detail  {(time.perf_counter() - t) * 1000:7.1f} ms")
    client.cookies.set("biq_csrf", "x")
    timings = []
    for _ in range(20):
        t = time.perf_counter()
        resp = client.post(
            "/v1/receipts/upload-init",
            json={
                "filename": "a.pdf",
                "content_type": "application/pdf",
                "size_bytes": 10,
                "sha256": uuid.uuid4().hex * 2,
                "allow_duplicate": True,
            },
            headers={"X-CSRF-Token": "x"},
        )
        timings.append((time.perf_counter() - t) * 1000)
        assert resp.status_code == 201, resp.text
    timings.sort()
    print(f"upload-init    p50={statistics.median(timings):7.1f} ms  p95={timings[18]:7.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=5000)
    parser.add_argument("--bench", action="store_true")
    args = parser.parse_args()
    _, token, _ = seed(args.lines)
    if args.bench:
        bench(token)


if __name__ == "__main__":
    main()
