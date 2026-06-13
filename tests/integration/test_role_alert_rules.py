"""Role-specific panel rules: GEN alerts → Distractors; PVG In-Review → Distractors."""

from __future__ import annotations

from datetime import UTC, datetime

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
        Alert("INC1", E, AlertState.ACKNOWLEDGED, NOW),   # unresolved → red
        Alert("INC2", E, AlertState.RESOLVED, NOW),       # resolved → yellow
    ])
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert sorted(t.color.value for t in panel.groups["Distractors"]) == ["red", "yellow"]
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
