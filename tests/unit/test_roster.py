"""Editable roster: add SREs + move between regions (#16)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from standup_dashboard import config
from standup_dashboard.services import roster
from standup_dashboard.storage.db import Database

ALEX = "alexandre.gomes@canonical.com"  # seed AMER engineer


def _db(tmp_path):
    return Database(tmp_path / "t.db")


def now():
    return datetime(2026, 6, 12, tzinfo=UTC)


def test_add_engineer_appears_in_region(tmp_path):
    db = _db(tmp_path)
    roster.add_engineer(db, "New Person", "New.Person@canonical.com", "EMEA", now())
    assert "new.person@canonical.com" in config.ENGINEERS_BY_EMAIL  # lowercased
    assert "new.person@canonical.com" in config.REGIONS["EMEA"].member_emails
    # Reloading from the DB reproduces the override.
    roster.load(db)
    assert config.ENGINEERS_BY_EMAIL["new.person@canonical.com"].name == "New Person"
    db.close()


def test_move_engineer_between_regions(tmp_path):
    db = _db(tmp_path)
    roster.move_engineer(db, ALEX, "EMEA", now())
    assert ALEX in config.REGIONS["EMEA"].member_emails
    assert ALEX not in config.REGIONS["AMER"].member_emails
    db.close()


def test_validation(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        roster.add_engineer(db, "", "a@b.com", "AMER", now())       # no name
    with pytest.raises(ValueError):
        roster.add_engineer(db, "X", "not-an-email", "AMER", now())  # bad email
    with pytest.raises(ValueError):
        roster.add_engineer(db, "X", "a@b.com", "NOPE", now())       # bad region
    with pytest.raises(ValueError):
        roster.move_engineer(db, "nobody@x.com", "AMER", now())      # unknown engineer
    db.close()
