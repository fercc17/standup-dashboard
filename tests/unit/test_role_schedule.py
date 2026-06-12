"""US2 unit tests: override expiry, weekly fallback (T030)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from standup_dashboard.services import schedule
from standup_dashboard.storage.db import Database

# An AMER engineer (America/Mexico_City, UTC-6 in June).
EMAIL = "alexandre.gomes@canonical.com"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_override_expires_at_region_local_midnight(db):
    # 2026-06-11 12:00 UTC ≈ 06:00 in Mexico City → next local midnight is
    # 2026-06-12 00:00 local = 2026-06-12 06:00 UTC.
    now = utc(2026, 6, 11, 12)
    schedule.set_today_override(db, EMAIL, "OFF", now)

    assert db.get_active_overrides(now) == {EMAIL: "OFF"}
    # Just before local midnight — still active.
    assert db.get_active_overrides(utc(2026, 6, 12, 5)) == {EMAIL: "OFF"}
    # After local midnight — expired.
    assert db.get_active_overrides(utc(2026, 6, 12, 6, 1)) == {}


def test_weekly_default_persists_latest_wins(db):
    now = utc(2026, 6, 11)
    schedule.set_weekly_role(db, EMAIL, "MON", "GEN", now)
    schedule.set_weekly_role(db, EMAIL, "MON", "BVG", utc(2026, 6, 11, 13))
    assert db.get_weekly_schedule()[(EMAIL, "MON")] == "BVG"


def test_set_weekly_role_validates(db):
    now = utc(2026, 6, 11)
    with pytest.raises(ValueError):
        schedule.set_weekly_role(db, EMAIL, "FUNDAY", "GEN", now)
    with pytest.raises(ValueError):
        schedule.set_weekly_role(db, EMAIL, "MON", "NOPE", now)
