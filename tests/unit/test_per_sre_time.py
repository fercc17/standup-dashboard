"""Per-SRE alert time & ticket time in the detail card (#167)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    CalendarAvail,
    GitHubPRStats,
    Pulse,
    Ticket,
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


def test_build_panel_reports_alert_and_ticket_time(tmp_path, db_dsn):
    db = Database(db_dsn)
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
    # A single incident: overlap and no-overlap agree (#173).
    assert panel.alert_union_seconds == 1800
    # All worklog here is ISReq, so it lands under "Jira ticket", none under ISDB.
    assert panel.jira_request_seconds == 5400
    assert panel.jira_project_seconds == 0
    db.close()


def test_alert_time_overlap_vs_union(tmp_path, db_dsn):
    # Two incidents this SRE resolved overlap in time. INC1 [10:00,10:10] = 10m,
    # INC2 [10:05,10:12] = 7m. Overlap sums both (17m); no-overlap merges the
    # shared 10:05–10:10 window → wall-clock 10:00–10:12 = 12m (#173).
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    at = lambda h, m: datetime(2026, 6, 12, h, m, tzinfo=UTC)  # noqa: E731
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        alerts=[
            Alert("INC1", E, AlertState.ACKNOWLEDGED, at(10, 0)),
            Alert("INC1", E, AlertState.RESOLVED, at(10, 10)),
            Alert("INC2", E, AlertState.ACKNOWLEDGED, at(10, 5)),
            Alert("INC2", E, AlertState.RESOLVED, at(10, 12)),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.alert_time_seconds == (10 + 7) * 60   # 17m, overlap double-counted
    assert panel.alert_union_seconds == 12 * 60        # 12m wall-clock
    db.close()


def test_distractor_share_folds_in_distraction_alert_time(tmp_path, db_dsn):
    # A GEN engineer's handled alerts classify as distractions (#158), so their
    # ack→resolve time folds into the distractor share alongside ticket worklog
    # (#distract-share). 30m of alert time against 5h open → 10%.
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        alerts=[
            Alert("INC1", E, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, 0, tzinfo=UTC)),
            Alert("INC1", E, AlertState.RESOLVED, datetime(2026, 6, 12, 18, 30, tzinfo=UTC)),
        ],
        calendar={E: CalendarAvail(has_data=True, open_seconds=5 * 3600)},
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.show_distractors is True
    assert panel.distractor_seconds == 1800                 # 30m distraction-alert time
    assert panel.distractor_share_label == "30m · 10% of open"


def test_distractor_share_excludes_pvg_alert_time(tmp_path, db_dsn):
    # The same alert handled as PVG (on-call) is real work, not a distraction — so it
    # must NOT count toward the distractor share (#distract-share).
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        alerts=[
            Alert("INC1", E, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, 0, tzinfo=UTC)),
            Alert("INC1", E, AlertState.RESOLVED, datetime(2026, 6, 12, 18, 30, tzinfo=UTC)),
        ],
        calendar={E: CalendarAvail(has_data=True, open_seconds=5 * 3600)},
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.distractor_seconds == 0
    assert panel.distractor_share_label == "0m · 0% of open"


def test_build_panel_reports_pr_stats(tmp_path, db_dsn):
    # The GH PRs lines reflect the per-engineer per-pulse PR activity carried on
    # the data (sourced from the GitHub fetch); zeros when unmapped/unconfigured (#173).
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    data = DashboardData(
        fetched_at=NOW, pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        github_prs={E: GitHubPRStats(created=4, merged=2, updated=6, reviewed=3)},
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert (panel.pr_stats.created, panel.pr_stats.merged,
            panel.pr_stats.updated, panel.pr_stats.reviewed) == (4, 2, 6, 3)
    # Unmapped engineer → all zeros, never a KeyError.
    other = build_panel(db, "colin.misare@canonical.com", data, NOW, region_key="AMER")
    assert other.pr_stats.created == 0
    db.close()


def test_panel_current_sprint_counts_and_links(tmp_path, db_dsn):
    # Per-project count of the engineer's tickets in the active sprint, + a Jira link
    # (#sprint-link). Active sprints come from the pulses (201 ISReq, 202 ISDB).
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    db.upsert_account_ids({E: "712020:abc-123"})      # accountId known
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW), Pulse("ISDB", 202, "s", NOW, NOW)],
        tickets=[
            Ticket("ISReq-1", "ISReq", "a", "To Do", None, assignee_email=E, sprint_id=201),
            Ticket("ISReq-2", "ISReq", "b", "In Progress", None, assignee_email=E, sprint_id=201),
            Ticket("ISDB-1", "ISDB", "c", "In Progress", None, assignee_email=E, sprint_id=202),
            # No sprint → not counted (the exact case the user hit).
            Ticket("ISDB-9", "ISDB", "d", "To Do", None, assignee_email=E, sprint_id=None),
            # Someone else's ticket in the sprint → not counted.
            Ticket("ISReq-3", "ISReq", "e", "To Do", None, assignee_email="other@x", sprint_id=201),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.sprint_isreq_count == 2          # ISReq-1, ISReq-2
    assert panel.sprint_isdb_count == 1           # ISDB-1 only (ISDB-9 has no sprint)
    # accountId-based sprint board links (board 1400 ISDB / 11304 ISReq).
    assert "/jira/software/c/projects/ISDB/boards/1400" in panel.sprint_isdb_url
    assert "assignee=712020%3Aabc-123" in panel.sprint_isdb_url
    assert "/jira/software/c/projects/ISREQ/boards/11304" in panel.sprint_isreq_url
    db.close()


def test_ticket_time_split_by_project(tmp_path, db_dsn):
    # ISDB worklog feeds "Jira project", ISReq worklog feeds "Jira ticket" (#173).
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "PVG", NOW)
    data = DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", 201, "s", NOW, NOW)],
        touches=[
            TouchEvent("ISDB-1", E, TouchKind.WORKLOG, datetime(2026, 6, 12, 17, tzinfo=UTC),
                       seconds=3600),
            TouchEvent("ISReq-9", E, TouchKind.WORKLOG, datetime(2026, 6, 11, 9, tzinfo=UTC),
                       seconds=1800),
        ],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    assert panel.jira_project_seconds == 3600          # ISDB
    assert panel.jira_request_seconds == 1800          # ISReq
    assert panel.ticket_time_seconds == 5400           # total, both projects
    db.close()
