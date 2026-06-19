"""Shared test fixtures: a configured app over an ephemeral PostgreSQL + secrets,
and a TestClient. External HTTP is mocked per-test with respx.

The storage layer is PostgreSQL (dev/prod parity with the charm), so each test
runs against a fresh, throwaway database spun up by ``pytest-postgresql`` — it
needs a PostgreSQL server binary (``pg_ctl``/``initdb``) on PATH.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from standup_dashboard.app import create_app
from standup_dashboard.storage.db import Database


@pytest.fixture(autouse=True)
def _reset_roster():
    """Roster overrides mutate process-wide config; reset to the seed per test."""
    from standup_dashboard import config
    config.rebuild_roster()
    yield
    config.rebuild_roster()


@pytest.fixture
def db_dsn(postgresql) -> str:
    """libpq DSN for a fresh, ephemeral PostgreSQL database (one per test)."""
    info = postgresql.info
    dsn = f"host={info.host} port={info.port} user={info.user} dbname={info.dbname}"
    if info.password:
        dsn += f" password={info.password}"
    return dsn


@pytest.fixture
def db(db_dsn: str) -> Iterator[Database]:
    """A ``Database`` bound to the per-test ephemeral PostgreSQL (schema applied)."""
    database = Database(db_dsn)
    yield database
    database.close()


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "jira_token.txt").write_text("jira-test-token", encoding="utf-8")
    (d / "pagerduty_token.txt").write_text("pd-test-token", encoding="utf-8")
    (d / "pagerduty_ical_url.txt").write_text("https://example.test/oncall.ics", encoding="utf-8")
    return d


@pytest.fixture
def app(db_dsn: str, secrets_dir: Path):
    return create_app(
        db_dsn=db_dsn,
        secrets_dir=str(secrets_dir),
        run_startup_validation=False,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
