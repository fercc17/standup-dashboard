"""Shared test fixtures: a configured app over temp dirs + secrets, and a
TestClient. External HTTP is mocked per-test with respx.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from standup_dashboard.app import create_app


@pytest.fixture(autouse=True)
def _reset_roster():
    """Roster overrides mutate process-wide config; reset to the seed per test."""
    from standup_dashboard import config
    config.rebuild_roster()
    yield
    config.rebuild_roster()


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "jira_token.txt").write_text("jira-test-token", encoding="utf-8")
    (d / "pagerduty_token.txt").write_text("pd-test-token", encoding="utf-8")
    (d / "pagerduty_ical_url.txt").write_text("https://example.test/oncall.ics", encoding="utf-8")
    return d


@pytest.fixture
def app(tmp_path: Path, secrets_dir: Path):
    return create_app(
        db_path=str(tmp_path / "dashboard.db"),
        secrets_dir=str(secrets_dir),
        snapshots_dir=str(tmp_path / "snapshots"),
        run_startup_validation=False,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
