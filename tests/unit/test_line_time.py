"""Per-line time log on the detail card (#line-time).

Each ticket row shows the worklog this engineer logged on it this pulse; each
alert row shows how long the incident lasted (fire→resolve) or, if still open,
how long it has been open (fire→now).
"""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    Pulse,
    Ticket,
    TouchEvent,
    TouchKind,
)
from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import DashboardData, build_panel

E = "alexandre.gomes@canonical.com"  # AMER
TZ = "America/Mexico_City"
NOW = datetime(2026, 6, 12, 18, tzinfo=UTC)  # Friday, inside the anchored pulse 12
SPRINT = 1


def _all_vms(panel):
    return [vm for vms in panel.groups.values() for vm in vms]


def test_ticket_row_shows_worklog_time_for_this_engineer(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", SPRINT, "s", NOW, NOW)],
        tickets=[Ticket("ISReq-1", "ISReq", "Do a thing", "In Progress", "Medium",
                        assignee_email=E, sprint_id=SPRINT)],
        touches=[
            TouchEvent("ISReq-1", E, TouchKind.WORKLOG,
                       datetime(2026, 6, 12, 9, tzinfo=UTC), seconds=3600),
            TouchEvent("ISReq-1", E, TouchKind.WORKLOG,
                       datetime(2026, 6, 12, 14, tzinfo=UTC), seconds=1800),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    row = next(vm for vm in _all_vms(panel) if vm.key == "ISReq-1")
    assert row.time_label == "1h 30m"   # 1h + 30m of worklog, summed for the pulse
    assert "logged" in row.time_title.lower()
    db.close()


def test_ticket_row_has_no_time_when_no_worklog(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", SPRINT, "s", NOW, NOW)],
        tickets=[Ticket("ISReq-9", "ISReq", "No work logged", "In Progress", "Medium",
                        assignee_email=E, sprint_id=SPRINT)],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    row = next(vm for vm in _all_vms(panel) if vm.key == "ISReq-9")
    assert row.time_label == ""
    db.close()


def test_alert_row_shows_lasted_for_resolved_and_open_for_ongoing(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    at = lambda h, m: datetime(2026, 6, 12, h, m, tzinfo=UTC)  # noqa: E731
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", SPRINT, "s", NOW, NOW)],
        alerts=[
            # INC1: fired 16:00, acked 16:10, resolved 16:40 → lasted 40m (fire→resolve).
            Alert("INC1", "", AlertState.TRIGGERED, at(16, 0)),
            Alert("INC1", E, AlertState.ACKNOWLEDGED, at(16, 10)),
            Alert("INC1", E, AlertState.RESOLVED, at(16, 40)),
            # INC2: fired 15:00, acked 15:05, never resolved → open 3h (fire→now=18:00).
            Alert("INC2", "", AlertState.TRIGGERED, at(15, 0)),
            Alert("INC2", E, AlertState.ACKNOWLEDGED, at(15, 5)),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    # Titles are "STATUS — #<n> — title"; here no number/title, so match on STATUS.
    by_state = {vm.title.split(" — ")[0]: vm for vm in _all_vms(panel) if vm.key == "⚠"}
    assert by_state["RES"].time_label == "40m"
    assert "lasted" in by_state["RES"].time_title.lower()
    assert by_state["ACK"].time_label == "3h"
    assert "open" in by_state["ACK"].time_title.lower()
    db.close()
