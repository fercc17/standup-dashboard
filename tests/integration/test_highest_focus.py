"""Highest-focus toggle: flag (don't move) off-focus in-progress ISReq (#focus-toggle)."""

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
                # A non-priority in-progress ISReq — the kind the toggle flags as
                # "wrong ticket" (not Highest / ps5-blocker / [PR/MP Review]).
                issue("ISReq-5", assignee=COLIN, status="In Progress", sprint_id=201),
                issue("ISReq-6", assignee=COLIN, status="Untriaged", sprint_id=201),
            ],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )


def test_highest_toggle_flags_offfocus_isreq(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    slot = region_weekday(now, AMER_TZ)
    app.state.ctx.db.set_weekly_role(COLIN, slot, "GEN", now)
    client.post("/refresh", data={"regions": "AMER"})

    off = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    # Toggle off: the ticket is shown, nothing flagged.
    assert "ISReq-5" in off and "flagged" not in off

    # Toggle on: the off-focus ISReq is flagged in place (not hidden) so it's easy
    # to spot someone on the wrong ticket — and it does not disappear.
    assert client.post("/toggle/highest", data={"value": "on"}).status_code == 204
    on = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISReq-5" in on and "flagged" in on
