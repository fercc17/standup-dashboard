"""US1 integration: fetch → persist → render chips + detail panel (T029).

Mocks Jira + PagerDuty with respx, drives /refresh and /chip/.../detail through
the real app, and asserts role-aware coloring and 24h counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.roles import region_weekday
from tests.fixtures.jira_pd import Scenario, install, issue

EMAIL = "alexandre.gomes@canonical.com"  # AMER engineer
AMER_TZ = "America/Mexico_City"


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start = now - timedelta(days=5)
    end = now + timedelta(days=5)
    touched_at = _jira_dt(now - timedelta(hours=1))
    acked_at = _jira_dt(now - timedelta(hours=2))

    status_change = [{
        "author": {"emailAddress": EMAIL},
        "created": touched_at,
        "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
    }]

    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB S1", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq S1", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={
            101: [issue("ISDB-1", assignee=EMAIL, status="In Progress", sprint_id=101)],
            201: [issue("ISReq-1", assignee=EMAIL, status="In Progress", sprint_id=201)],
        },
        # A distractor: engineer touched it but it isn't assigned to them.
        search_issues=[
            issue("ISReq-9", assignee="someone.else@canonical.com", status="In Progress",
                  changelog=status_change),
        ],
        users=[{"id": "PU1", "email": EMAIL, "name": "Alexandre Gomes"}],
        incidents=[{"id": "INC1"}],
        log_entries={
            "INC1": [{
                "type": "acknowledge_log_entry",
                "agent": {"id": "PU1"},
                "created_at": acked_at,
            }],
        },
    )


def test_us1_refresh_and_detail(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))

    # Pin today's role to Project so assigned ISDB→green, assigned ISReq→red.
    slot = region_weekday(now, AMER_TZ)
    app.state.ctx.db.set_weekly_role(EMAIL, slot, "Project", now)

    # Refresh kicks off a background fetch (completes before the test client
    # returns) and renders the "refreshing" fragment with the roster.
    resp = client.post("/refresh", data={"regions": "AMER"})
    assert resp.status_code == 200
    assert "Refreshing" in resp.text

    # The page then reflects the fetched data.
    page = client.get("/", params={"regions": "AMER"}).text
    assert "Alexandre Gomes" in page
    assert "Project" in page          # role tag
    assert "1/0" in page              # alerts ack/resolved (24h)

    # Detail panel: coloring per role.
    detail = client.get(f"/chip/{EMAIL}/detail", params={"regions": "AMER"})
    assert detail.status_code == 200
    panel = detail.text
    assert "ISDB-1" in panel and "ISReq-1" in panel and "ISReq-9" in panel
    assert "c-green" in panel         # assigned ISDB under Project
    assert "c-red" in panel           # assigned ISReq + distractor under Project
    # Each Jira ticket title/key links out to the issue (#89).
    assert 'href="https://warthogs.atlassian.net/browse/ISDB-1"' in panel
    assert 'target="_blank"' in panel
