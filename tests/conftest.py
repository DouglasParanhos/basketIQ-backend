from __future__ import annotations

import os

# Configuration must be in place before any basketiq module reads settings.
os.environ.update(
    {
        "BASKETIQ_ENV": "test",
        "BASKETIQ_DATABASE_URL": os.environ.get(
            "BASKETIQ_TEST_DATABASE_URL",
            "postgresql+psycopg://basketiq:basketiq@localhost:5432/basketiq_test",
        ),
        "BASKETIQ_STORAGE_BACKEND": "memory",
        "BASKETIQ_QUEUE_BACKEND": "inline",
        "BASKETIQ_OCR_ENGINE": "none",
        "BASKETIQ_OIDC_ISSUER": "https://idp.test",
        "BASKETIQ_OIDC_CLIENT_ID": "basketiq-test",
        "BASKETIQ_OIDC_CLIENT_SECRET": "test-secret",
        "BASKETIQ_OIDC_REDIRECT_URI": "http://testserver/v1/auth/callback",
        "BASKETIQ_FRONTEND_BASE_URL": "http://frontend.test",
        "BASKETIQ_ADMIN_EMAILS": "admin@example.test",
        "BASKETIQ_LOG_LEVEL": "WARNING",
    }
)

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from basketiq.config import get_settings
from basketiq.db.session import get_sessionmaker
from tests.helpers import FakeOcr


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: needs PostgreSQL")


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    url = get_settings().database_url
    _ensure_database(url)
    from basketiq.cli import migrate

    migrate()
    yield


TABLES = (
    "audit_events, merchant_configs, product_aliases, receipt_lines, processing_jobs, receipts, "
    "products, user_sessions, users"
)


@pytest.fixture
def db(database: None) -> Iterator[Session]:
    session = get_sessionmaker()()
    yield session
    session.rollback()
    session.close()
    with get_sessionmaker()() as cleanup:
        cleanup.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        cleanup.commit()


@pytest.fixture
def storage() -> Iterator[Any]:
    from basketiq.storage import get_storage

    s = get_storage()
    s.objects.clear()  # type: ignore[attr-defined]
    s.available = True  # type: ignore[attr-defined]
    yield s
    s.objects.clear()  # type: ignore[attr-defined]
    s.available = True  # type: ignore[attr-defined]


@pytest.fixture
def queue() -> Iterator[Any]:
    from basketiq.queue import get_queue

    q = get_queue()
    q.pending.clear()  # type: ignore[attr-defined]
    yield q
    q.pending.clear()  # type: ignore[attr-defined]


@pytest.fixture
def client(db: Session, storage: Any, queue: Any) -> Iterator[TestClient]:
    from basketiq.api.main import app
    from basketiq.api.ratelimit import limiter

    limiter.reset()
    with TestClient(app, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def login(db: Session) -> Callable[..., Any]:
    """Create a user + session directly and put the session cookies on a client."""
    from basketiq.auth.sessions import create_session
    from basketiq.db.models import User

    def _login(c: TestClient, email: str | None = None, *, consent: bool = True, role: str = "user") -> User:
        from datetime import UTC, datetime

        settings = get_settings()
        with get_sessionmaker()() as s:
            user = User(
                google_subject=f"https://idp.test|{uuid.uuid4()}",
                email=email or f"{uuid.uuid4().hex[:8]}@example.test",
                role=role,
                privacy_accepted_at=datetime.now(UTC) if consent else None,
                privacy_version="2026-10-15" if consent else None,
            )
            s.add(user)
            s.flush()
            tokens = create_session(s, settings, user)
            s.commit()
            s.refresh(user)
            s.expunge(user)
        c.cookies.set("biq_at", tokens.access_token)
        c.cookies.set("biq_rt", tokens.refresh_token, path="/v1/auth")
        c.cookies.set("biq_csrf", tokens.csrf_token)
        c.headers["X-CSRF-Token"] = tokens.csrf_token
        return user

    return _login


@pytest.fixture
def run_jobs(storage: Any, queue: Any) -> Callable[..., int]:
    """Drain the inline queue through the real worker code path."""
    from basketiq.services.processing import Components, process_job

    def _run(ocr: Any = None, resolver: Any = None, external: Any = None, max_rounds: int = 10) -> int:
        settings = get_settings()
        components = Components(ocr=ocr or FakeOcr([]), resolver=resolver, external=external)
        processed = 0
        for _ in range(max_rounds):
            ids = queue.drain()
            if not ids:
                break
            for job_id in ids:
                process_job(
                    job_id,
                    sessions=get_sessionmaker(),
                    settings=settings,
                    storage=storage,
                    queue=queue,
                    components=components,
                )
                processed += 1
        return processed

    return _run
