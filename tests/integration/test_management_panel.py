"""A manager's detail panel: To Do / WIP / Done only, no Distractors (#72 follow-up)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fixtures.jira_pd import Scenario, install, issue

FERNANDO = "fernando.carrillo.castro@canonical.com"  # manager


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=5), now + timedelta(days=5)
    touched = [{
        "author": {"emailAddress": FERNANDO},
        "created": _jira_dt(now - timedelta(hours=1)),
        "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
    }]
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
            201: [issue("ISReq-1", assignee=FERNANDO, status="In Progress", sprint_id=201)],
        },
        # A ticket the manager touched but isn't assigned → would be a distractor.
        search_issues=[
            issue("ISReq-9", assignee="someone.else@canonical.com", status="In Progress",
                  changelog=touched),
        ],
        users=[{"id": "PU1", "email": FERNANDO, "name": "Fernando Carrillo Castro"}],
    )


def test_manager_panel_is_clickable_and_has_no_distractors(client, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    client.post("/refresh", data={"regions": "AMER"})

    # The Management chip is clickable now → opening the panel returns 200.
    panel = client.get(f"/chip/{FERNANDO}/detail", params={"regions": "AMER"})
    assert panel.status_code == 200
    body = panel.text
    assert "ISReq-1" in body            # assigned work shows (WIP)
    assert "Distractors" not in body    # managers have no Distractors group
    assert "ISReq-9" not in body        # touched-not-assigned is dropped, not a distractor
    assert "Success" in body            # To Do / WIP / Success groups still render
