"""Role-specific panel rules: GEN alerts → Distractors; PVG In-Review → Distractors."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, Pulse, Ticket
from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.services import schedule
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import DashboardData, build_panel

E = "alexandre.gomes@canonical.com"  # AMER
TZ = "America/Mexico_City"
NOW = datetime(2026, 6, 12, 18, tzinfo=UTC)  # Friday


def _db_with_role(tmp_path, role):
    db = Database(tmp_path / "t.db")
    db.set_weekly_role(E, region_weekday(NOW, TZ), role, NOW)
    return db


def test_gen_alerts_go_to_distractors(tmp_path):
    db = _db_with_role(tmp_path, "GEN")
    data = DashboardData(fetched_at=NOW, pulses=[Pulse("ISReq", 201, "s", NOW, NOW)], alerts=[
        Alert("INC1", E, AlertState.ACKNOWLEDGED, NOW),
        Alert("INC2", E, AlertState.RESOLVED, NOW),
    ])
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    # GEN's alerts are a distraction and coloured by role → both yellow (#143).
    assert sorted(t.color.value for t in panel.groups["Distractors"]) == ["yellow", "yellow"]
    assert panel.groups["Success"] == []   # resolved alert isn't a Success for GEN
    assert panel.groups["WIP"] == []
    db.close()


def test_pvg_in_review_is_yellow_distractor(tmp_path):
    db = _db_with_role(tmp_path, "PVG")
    data = DashboardData(fetched_at=NOW, pulses=[Pulse("ISReq", 201, "s", NOW, NOW)], tickets=[
        Ticket("ISReq-1", "ISReq", "x", "In Review", None, assignee_email=E, sprint_id=201),
    ])
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    dist = {t.key: t.color.value for t in panel.groups["Distractors"]}
    assert dist.get("ISReq-1") == "yellow"
    assert all(t.key != "ISReq-1" for t in panel.groups["WIP"])
    db.close()


def test_project_completed_isreq_is_red_distractor(tmp_path):
    # A Project engineer should only work ISDB: a completed ISReq is off-task, so
    # it moves out of Success into Distractors as RED, while a completed ISDB
    # stays a Success.
    db = _db_with_role(tmp_path, "Project")
    data = DashboardData(fetched_at=NOW, pulses=[Pulse("ISReq", 201, "s", NOW, NOW)], tickets=[
        Ticket("ISReq-1", "ISReq", "x", "Done", None, assignee_email=E, sprint_id=201,
               is_done_date=date(2026, 6, 12)),
        Ticket("ISDB-1", "ISDB", "y", "Done", None, assignee_email=E, sprint_id=None,
               is_done_date=date(2026, 6, 12)),
    ])
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    dist = {t.key: t.color.value for t in panel.groups["Distractors"]}
    succ = {t.key for t in panel.groups["Success"]}
    assert dist.get("ISReq-1") == "red"     # off-task completed ISReq → red distractor
    assert "ISReq-1" not in succ
    assert "ISDB-1" in succ                  # the engineer's ISDB completion is a success
    db.close()


def test_pvg_in_review_yellow_even_with_highest_focus(tmp_path):
    # The Highest-only toggle would normally make a non-priority ISReq red, but
    # PVG's In-Review rule wins → still yellow (regression for Afif).
    db = _db_with_role(tmp_path, "PVG")
    schedule.set_highest_focus(db, True, NOW)
    data = DashboardData(fetched_at=NOW, pulses=[Pulse("ISReq", 201, "s", NOW, NOW)], tickets=[
        Ticket("ISReq-1", "ISReq", "x", "In Review", None, assignee_email=E, sprint_id=201),
    ])
    panel = build_panel(db, E, data, NOW, region_key="AMER", highest_focus=True)
    dist = {t.key: t.color.value for t in panel.groups["Distractors"]}
    assert dist.get("ISReq-1") == "yellow"
    db.close()
