"""Data migration f6b9c4e2a3d8: accounts that already consented skip the onboarding (RF-14).

Runs Alembic in a subprocess against a throwaway database, so the shared test database is untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from basketiq.config import get_settings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "e5a8b3d1f2c7"  # creates user_profiles
AFTER = "f6b9c4e2a3d8"  # marks the existing accounts as done
EARLIER = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def alembic(url: str, *args: str) -> None:
    env = os.environ | {"BASKETIQ_DATABASE_URL": url}
    cmd = [sys.executable, "-m", "alembic", *args]
    run = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)  # noqa: S603  fixed command
    assert run.returncode == 0, run.stderr


@pytest.fixture
def throwaway() -> Iterator[tuple[str, Engine]]:
    target = make_url(get_settings().database_url)
    name = f"basketiq_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = target.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        yield url, engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def add_user(engine: Engine, label: str, *, consented: bool) -> uuid.UUID:
    user_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users"
                " (id, google_subject, email, role, status, privacy_accepted_at, privacy_version)"
                " VALUES (:id, :sub, :email, 'user', 'active', :at, :version)"
            ),
            {
                "id": user_id,
                "sub": f"https://idp.test|{user_id}",
                "email": f"{label}@example.test",
                "at": EARLIER if consented else None,
                "version": "2026-10" if consented else None,
            },
        )
    return user_id


def profiles(engine: Engine) -> dict[uuid.UUID, Any]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM user_profiles")).mappings().all()
    return {row["user_id"]: row for row in rows}


@pytest.mark.req("F-118")
def test_accounts_that_consented_are_marked_done_with_an_empty_profile(
    throwaway: tuple[str, Engine],
) -> None:
    url, engine = throwaway
    alembic(url, "upgrade", BEFORE)
    old = add_user(engine, "old", consented=True)
    add_user(engine, "never-consented", consented=False)

    alembic(url, "upgrade", AFTER)

    found = profiles(engine)
    assert set(found) == {old}  # no profile for an account that never reached the app
    row = found[old]
    assert row["onboarding_completed_at"] is not None
    # Nothing is invented for the person (constitution III): every value stays empty.
    assert (row["household_size"], row["monthly_goal_cents"]) == (None, None)
    assert (row["merchants"], row["goals"]) == ([], [])


@pytest.mark.req("F-118")
def test_an_unfinished_profile_is_completed_and_a_finished_one_keeps_its_date_and_data(
    throwaway: tuple[str, Engine],
) -> None:
    url, engine = throwaway
    alembic(url, "upgrade", BEFORE)
    unfinished = add_user(engine, "unfinished", consented=True)
    finished = add_user(engine, "finished", consented=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_profiles (user_id, household_size, merchants, onboarding_completed_at)"
                " VALUES (:u, 3, '[\"lidl\"]', NULL), (:f, 2, '[\"aldi\"]', :done)"
            ),
            {"u": unfinished, "f": finished, "done": EARLIER},
        )

    alembic(url, "upgrade", AFTER)

    found = profiles(engine)
    assert found[unfinished]["onboarding_completed_at"] is not None
    assert (found[unfinished]["household_size"], found[unfinished]["merchants"]) == (3, ["lidl"])
    assert found[finished]["onboarding_completed_at"] == EARLIER
    assert (found[finished]["household_size"], found[finished]["merchants"]) == (2, ["aldi"])


@pytest.mark.req("F-118")
def test_downgrade_and_upgrade_again_keep_the_original_dates(throwaway: tuple[str, Engine]) -> None:
    url, engine = throwaway
    alembic(url, "upgrade", BEFORE)
    old = add_user(engine, "old", consented=True)
    alembic(url, "upgrade", AFTER)
    first = profiles(engine)[old]["onboarding_completed_at"]

    alembic(url, "downgrade", BEFORE)
    assert profiles(engine)[old]["onboarding_completed_at"] == first  # the downgrade removes nothing
    alembic(url, "upgrade", AFTER)

    assert profiles(engine)[old]["onboarding_completed_at"] == first
