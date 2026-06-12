"""US4 integration: AMER+APAC combined view + Management group (T046, #72)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fixtures.jira_pd import Scenario, install, issue

FERNANDO = "fernando.carrillo.castro@canonical.com"  # AMER + APAC manager


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=3), now + timedelta(days=3)
    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={
            101: [issue("ISDB-1", assignee=FERNANDO, status="In Progress", sprint_id=101)],
            201: [],
        },
        users=[{"id": "PU1", "email": FERNANDO, "name": "Fernando Carrillo Castro"}],
    )


def test_us4_managers_grouped_under_management_not_regions(client, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))

    client.post("/refresh", data={"regions": ["AMER", "APAC"]})
    page = client.get("/", params=[("regions", "AMER"), ("regions", "APAC")]).text

    # Both region group headers render.
    assert ">AMER " in page or ">AMER<" in page
    assert "APAC" in page
    # Managers are no longer region members: Fernando shows once, under the
    # dedicated Management group (#72), not once per region.
    assert "Management" in page
    assert page.count("Fernando Carrillo") == 1
    # Global management is shown in the same group.
    assert "Kristofer Tingdahl" in page
