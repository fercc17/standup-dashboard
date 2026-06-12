"""Unit tests for the spreadsheet schedule paste (#71)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.services.schedule import (
    PasteAction,
    apply_schedule_paste,
    parse_schedule_paste,
)
from standup_dashboard.storage.db import Database

ALEX = "alexandre.gomes@canonical.com"
COLIN = "colin.misare@canonical.com"


def test_parse_roles_and_notes():
    text = (
        "Date\tAlexandre Gomes\tColin Misare\n"
        "Mon, Jun 08\tPVG\tBVG | 1:1 at 3pm\n"
        "Tue, Jun 09\tGEN\t| OoO\n"
    )
    actions, errors = parse_schedule_paste(text)
    assert errors == []
    got = {(a.email, a.weekday): (a.role, a.note) for a in actions}
    assert got[(ALEX, "MON")] == ("PVG", None)
    assert got[(COLIN, "MON")] == ("BVG", "1:1 at 3pm")
    assert got[(ALEX, "TUE")] == ("GEN", None)
    assert got[(COLIN, "TUE")] == (None, "OoO")  # note-only cell


def test_parse_matches_engineer_by_email_too():
    text = f"Date\t{ALEX}\nThu, Jun 12\tProject\n"
    actions, errors = parse_schedule_paste(text)
    assert errors == []
    assert actions == [PasteAction(email=ALEX, weekday="THU", role="Project", note=None)]


def test_parse_skips_weekend_rows():
    text = "Date\tAlexandre Gomes\nSat, Jun 13\tOFF\nSun, Jun 14\tOFF\n"
    actions, errors = parse_schedule_paste(text)
    assert actions == []
    assert errors == []


def test_parse_reports_unknown_engineer_and_role():
    text = "Date\tNobody Here\tColin Misare\nMon, Jun 08\tPVG\tNOPE\n"
    actions, errors = parse_schedule_paste(text)
    assert any("Nobody Here" in e for e in errors)
    assert any("NOPE" in e for e in errors)
    # The known column had an invalid role and no note → nothing applied.
    assert actions == []


def test_parse_requires_header_plus_data():
    actions, errors = parse_schedule_paste("Mon\tPVG")
    assert actions == []
    assert errors


def test_apply_persists_roles_and_notes(tmp_path):
    db = Database(tmp_path / "t.db")
    now = datetime(2026, 6, 8, 12, tzinfo=UTC)
    text = "Date\tColin Misare\nMon, Jun 08\tBVG | review day\n"
    summary = apply_schedule_paste(db, text, now)
    assert summary == {"roles": 1, "notes": 1, "errors": []}
    assert db.get_weekly_schedule()[(COLIN, "MON")] == "BVG"
    assert db.get_day_notes()[(COLIN, "MON")] == "review day"
    db.close()
