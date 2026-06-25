"""Per-source refresh schedule: cron due-sources + per-source fetch status (#per-source-schedule)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard import config
from standup_dashboard.storage.db import Database


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 6, 25, h, m, tzinfo=UTC)


def test_due_sources_per_minute():
    assert config.due_sources(_at(10, 13)) == frozenset({"jira", "pagerduty", "github"})
    assert config.due_sources(_at(10, 30)) == frozenset({"jira", "pagerduty"})
    assert config.due_sources(_at(10, 58)) == frozenset({"jira", "pagerduty", "github"})
    assert config.due_sources(_at(10, 23)) == frozenset({"github"})
    assert config.due_sources(_at(10, 44)) == frozenset({"github"})
    assert config.due_sources(_at(10, 45)) == frozenset({"calendar"})
    assert config.due_sources(_at(10, 7)) == frozenset()         # nothing due


def test_ical_is_daily_at_midnight():
    assert "ical" in config.due_sources(_at(0, 0))
    assert "ical" not in config.due_sources(_at(1, 0))           # other hours
    assert "ical" not in config.due_sources(_at(0, 13))          # other minutes


def test_latest_source_ok_tracks_each_source_independently(db_dsn):
    db = Database(db_dsn)
    t1 = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 25, 10, 13, tzinfo=UTC)
    full = db.create_fetch_snapshot(t1, jira_ok=True, pagerduty_ok=True, ical_ok=True)
    # A later PD-only fetch that FAILED PD and didn't attempt Jira/iCal (NULL).
    db.create_fetch_snapshot(t2, jira_ok=None, pagerduty_ok=False, ical_ok=None)

    assert db.latest_source_ok("jira_ok") is True       # last Jira attempt (the full one) ok
    assert db.latest_source_ok("pagerduty_ok") is False  # last PD attempt failed
    assert db.latest_source_ok("ical_ok") is True
    # The Jira incremental anchor stays on the full fetch — a non-Jira fetch never
    # truncates the window to nothing.
    assert db.latest_good_fetch().id == full
    db.close()


def test_latest_source_ok_none_when_never_attempted(db_dsn):
    db = Database(db_dsn)
    db.create_fetch_snapshot(_at(10, 0), jira_ok=True, pagerduty_ok=None, ical_ok=None)
    assert db.latest_source_ok("pagerduty_ok") is None  # never attempted → not a failure
    db.close()
