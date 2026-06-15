"""Per-SRE alert time & ticket time in the detail card (#167)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    Pulse,
    TouchEvent,
    TouchKind,
    hours_label,
)
from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.services.touches import extract_touches
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import DashboardData, build_panel
from tests.fixtures.jira_pd import issue

E = "alexandre.gomes@canonical.com"  # AMER
TZ = "America/Mexico_City"
NOW = datetime(2026, 6, 12, 18, tzinfo=UTC)  # Friday, inside the anchored pulse


def _jira_dt(d, h=12):
    return datetime(2026, 6, d, h, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def test_hours_label():
    assert hours_label(0) == "0m"
    assert hours_label(None) == "0m"
    assert hours_label(45 * 60) == "45m"
    assert hours_label(3600) == "1h"
    assert hours_label(2 * 3600) == "2h"
    assert hours_label(6 * 3600 + 30 * 60) == "6h 30m"
    assert hours_label(32 * 3600) == "32h"  # never rolls into days


def test_extract_touches_worklog_time_goes_to_assignee():
    # Tempo logs worklogs under a bot author; the duration must be attributed to
    # the ticket's assignee (proxy), not the bot.
    iss = issue("ISReq-1", assignee=E)
    worklogs = [
        {"author": {"displayName": "Timesheets by Tempo"},
         "started": _jira_dt(12, 10), "timeSpentSeconds": 3600},
        {"author": {"displayName": "Timesheets by Tempo"},
         "started": _jira_dt(12, 14), "timeSpentSeconds": 1800},
    ]
    touches = extract_touches(
        iss, worklogs=worklogs,
        window_start=datetime(2026, 6, 8, tzinfo=UTC),
        window_end=datetime(2026, 6, 15, tzinfo=UTC),
        roster_emails={E},
    )
    wl = [t for t in touches if t.kind is TouchKind.WORKLOG]
    assert {t.engineer_email for t in wl} == {E}     # attributed to the assignee
    assert sum(t.seconds for t in wl) == 5400         # 1h + 30m


def test_extract_touches_worklog_skipped_when_assignee_not_in_roster():
    iss = issue("ISReq-2", assignee="someone.else@external.com")
    worklogs = [{"author": {"displayName": "Tempo"},
                 "started": _jira_dt(12), "timeSpentSeconds": 3600}]
    touches = extract_touches(
        iss, worklogs=worklogs,
        window_start=datetime(2026, 6, 8, tzinfo=UTC),
        window_end=datetime(2026, 6, 15, tzinfo=UTC),
        roster_emails={E},
    )
    assert [t for t in touches if t.kind is TouchKind.WORKLOG] == []


def test_build_panel_reports_alert_and_ticket_time(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        touches=[
            TouchEvent("ISReq-1", E, TouchKind.WORKLOG, datetime(2026, 6, 12, 17, tzinfo=UTC),
                       seconds=3600),
            TouchEvent("ISReq-2", E, TouchKind.WORKLOG, datetime(2026, 6, 11, 9, tzinfo=UTC),
                       seconds=1800),
            # A status touch carries no time and must not count toward ticket time.
            TouchEvent("ISReq-3", E, TouchKind.STATUS, datetime(2026, 6, 12, 9, tzinfo=UTC)),
        ],
        alerts=[
            Alert("INC1", E, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, 0, tzinfo=UTC)),
            Alert("INC1", E, AlertState.RESOLVED, datetime(2026, 6, 12, 18, 30, tzinfo=UTC)),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.ticket_time_seconds == 5400       # 1h + 30m of worklog
    assert panel.ticket_time_label == "1h 30m"
    assert panel.alert_time_seconds == 1800        # 30m ack→resolve
    assert panel.alert_time_label == "30m"
    db.close()
