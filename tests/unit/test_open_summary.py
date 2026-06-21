"""Team-wide open-work summary line shown above the regions (#summary)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    PRIORITY_HIGHEST,
    Alert,
    AlertState,
    Pulse,
    Ticket,
)
from standup_dashboard.web.presenters import DashboardData, build_open_summary

E = "alexandre.gomes@canonical.com"
SPRINT = 1


def _dt(d):
    return datetime(2026, 6, d, 12, tzinfo=UTC)


def _t(tid, status, priority=None, labels=None, sprint=SPRINT, title="x"):
    return Ticket(tid, "ISReq", title, status, priority,
                  labels=labels or [], sprint_id=sprint)


def test_open_summary_counts_open_work_and_ongoing_alerts():
    tickets = [
        _t("ISReq-1", "In Progress", PRIORITY_HIGHEST),                  # open Highest (WIP)
        _t("ISReq-2", "To Do", "Medium", labels=["ps5-blocker"]),       # open ps5 (To Do)
        _t("ISReq-3", "In Progress", title="[PR/MP Review] foo"),        # open PR/MP
        _t("ISReq-4", "Done", PRIORITY_HIGHEST),                         # Done → excluded
        _t("ISReq-5", "To Do", PRIORITY_HIGHEST, sprint=None),          # no sprint → out of scope
        _t("ISReq-6", "In Progress", PRIORITY_HIGHEST,                   # open ps5 + Highest
           labels=["ps5-blocker"]),
        _t("ISReq-7", "Escalated"),                                      # escalated (in sprint)
        _t("ISReq-8", "Escalated", sprint=None),                         # escalated, no sprint
    ]
    alerts = [
        Alert("INC1", E, AlertState.ACKNOWLEDGED, _dt(10)),             # ongoing
        Alert("INC2", E, AlertState.ACKNOWLEDGED, _dt(10)),
        Alert("INC2", E, AlertState.RESOLVED, _dt(11)),                 # resolved → not ongoing
    ]
    data = DashboardData(
        fetched_at=_dt(12), tickets=tickets, alerts=alerts,
        pulses=[Pulse("ISReq", SPRINT, "s", _dt(8), _dt(20))],
    )
    s = build_open_summary(data)
    assert (s.highest, s.ps5, s.ps5_highest, s.pr_mp, s.ongoing_alerts) == (2, 2, 1, 1, 1)
    # Escalated counts every ISReq ticket in the Escalated status, including the
    # one with no sprint — escalation is tracked across sprints, not just open work.
    assert s.escalated == 2


def test_open_summary_empty():
    s = build_open_summary(DashboardData(fetched_at=_dt(12)))
    assert (s.highest, s.ps5, s.ps5_highest, s.pr_mp, s.escalated, s.ongoing_alerts) == (
        0, 0, 0, 0, 0, 0)


def test_open_summary_carries_deep_links():
    # Each count links to its live source: a saved Jira filter per ticket
    # category, and the PagerDuty open-incident list for alerts (#summary-links).
    s = build_open_summary(DashboardData(fetched_at=_dt(12)))
    assert s.highest_url.endswith("/issues/?filter=39785")
    assert s.ps5_url.endswith("/issues/?filter=39782")
    assert s.ps5_highest_url.endswith("/issues/?filter=40098")
    assert s.pr_mp_url.endswith("/issues/?filter=40086")
    # Escalated links via JQL (no saved filter), URL-encoded.
    assert "/issues/?jql=" in s.escalated_url
    assert "project%20%3D%20ISREQ" in s.escalated_url
    assert "status%20%3D%20%22Escalated%22" in s.escalated_url
    assert s.alerts_url.startswith("https://canonical.pagerduty.com/incidents")
    assert "status=triggered,acknowledged" in s.alerts_url
    assert "PQ4ZG3S" in s.alerts_url  # scoped to the roster's PagerDuty team
