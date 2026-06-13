"""A board with several concurrent active sprints: the fetch pulls them all."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fixtures.jira_pd import Scenario, install, issue

COLIN = "colin.misare@canonical.com"  # AMER


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def test_fetch_pulls_issues_from_every_active_sprint(client, app, respx_mock):
    now = datetime.now(UTC)
    start, end = now - timedelta(days=5), now + timedelta(days=5)
    sprint = lambda sid, name: {  # noqa: E731
        "id": sid, "name": name, "startDate": _jira_dt(start),
        "endDate": _jira_dt(end), "state": "active",
    }
    scenario = Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        # The ISDB board (1) runs the shared sprint 101 AND its own sprint 102.
        sprints={1: [sprint(101, "shared"), sprint(102, "isdb")], 2: sprint(101, "shared")},
        sprint_issues={
            101: [issue("ISReq-1", assignee=COLIN, status="In Progress", sprint_id=101)],
            # ISDB-9 lives ONLY in the board's second active sprint and is not in
            # the candidate-search window — it is reachable only by fetching 102.
            102: [issue("ISDB-9", assignee=COLIN, status="Triaged", sprint_id=102)],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )
    install(respx_mock, scenario)
    client.post("/refresh", data={"regions": "AMER"})

    detail = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISDB-9" in detail   # only reachable via the board's 2nd active sprint
    assert "ISReq-1" in detail
