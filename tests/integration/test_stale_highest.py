"""A Highest ticket open longer than one pulse is flagged stale (#18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.roles import region_weekday
from tests.fixtures.jira_pd import Scenario, install, issue

COLIN = "colin.misare@canonical.com"  # AMER
AMER_TZ = "America/Mexico_City"


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=5), now + timedelta(days=5)
    old = _jira_dt(now - timedelta(days=20))   # > 1 pulse (14d) ago → stale
    fresh = _jira_dt(now - timedelta(days=2))   # recent → not stale
    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={
            101: [],
            201: [
                issue("ISReq-OLD", assignee=COLIN, status="In Progress", priority="Highest",
                      sprint_id=201, created=old),
                issue("ISReq-NEW", assignee=COLIN, status="In Progress", priority="Highest",
                      sprint_id=201, created=fresh),
            ],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )


def test_highest_open_over_a_pulse_is_flagged_stale(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    slot = region_weekday(now, AMER_TZ)
    app.state.ctx.db.set_weekly_role(COLIN, slot, "GEN", now)  # keep them in WIP
    client.post("/refresh", data={"regions": "AMER"})

    panel = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISReq-OLD" in panel and "ISReq-NEW" in panel
    # The >20d-old Highest ticket is flagged; the 2d-old one is not.
    assert "&gt;1 pulse" in panel or ">1 pulse" in panel
    assert panel.count("badge stale") == 1
