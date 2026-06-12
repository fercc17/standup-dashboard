"""Unit tests for the spreadsheet schedule paste (#71 transpose + rules)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.services.schedule import apply_schedule_paste, parse_schedule_paste
from standup_dashboard.storage.db import Database

AFIF = "afif.refrizal@canonical.com"
ALEXG = "alexandre.gomes@canonical.com"
ALEXL = "alex.lukens@canonical.com"
COLIN = "colin.misare@canonical.com"
MATT = "matheus.carvalho@canonical.com"
NICK = "nikolaos.sakkos@canonical.com"


def test_parse_aliases_rightalign_and_project():
    # The manager's real format: short names + a leading 'OK' status cell.
    text = (
        "Date\tAfif\tAlejdg\tAlex L\tColin\tMatt\tNick\n"
        "Wed, Jun 10\tOK\tPVG\tGEN\tPS7+\tBVG\tOFF\tPS7+\n"
    )
    actions, errors = parse_schedule_paste(text)
    assert errors == []
    got = {a.email: (a.weekday, a.role, a.note) for a in actions}
    assert got[AFIF] == ("WED", "PVG", None)       # first-name match
    assert got[ALEXG] == ("WED", "GEN", None)      # alias "Alejdg"
    assert got[ALEXL] == ("WED", "Project", "PS7+")  # alias "Alex L", PS7+→Project+note
    assert got[COLIN] == ("WED", "BVG", None)
    assert got[MATT] == ("WED", "OFF", None)       # alias "Matt"
    assert got[NICK] == ("WED", "Project", "PS7+")  # alias "Nick", on a project


def test_blank_cells_skipped_and_weekend_ignored():
    text = "Date\tColin\tNick\nMon, Jun 08\tBVG\t\nSat, Jun 13\tOFF\tOFF\n"
    actions, errors = parse_schedule_paste(text)
    assert errors == []
    # Only Colin's Monday BVG; Nick's blank skipped; the Saturday row ignored.
    assert [(a.email, a.weekday, a.role) for a in actions] == [(COLIN, "MON", "BVG")]


def test_unknown_engineer_header_reported():
    text = "Date\tColin\tNobodyX\nWed, Jun 10\tBVG\tGEN\n"
    actions, errors = parse_schedule_paste(text)
    assert any("NobodyX" in e for e in errors)
    assert {a.email for a in actions} == {COLIN}  # the unknown column is skipped


def test_requires_header_plus_data():
    actions, errors = parse_schedule_paste("Wed, Jun 10\tBVG")
    assert actions == []
    assert errors


def test_apply_persists_roles_and_project_notes(tmp_path):
    db = Database(tmp_path / "t.db")
    now = datetime(2026, 6, 10, 12, tzinfo=UTC)
    text = "Date\tColin\tNick\nWed, Jun 10\tBVG\tPS8\n"
    summary = apply_schedule_paste(db, text, now)
    assert summary == {"roles": 2, "notes": 1, "errors": []}
    assert db.get_weekly_schedule()[(COLIN, "WED")] == "BVG"
    assert db.get_weekly_schedule()[(NICK, "WED")] == "Project"
    assert db.get_day_notes()[(NICK, "WED")] == "PS8"
    db.close()
