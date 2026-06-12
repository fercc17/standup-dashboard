"""US4 integration: AMER+APAC combined view + Global group (T046)."""

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


def test_us4_manager_appears_under_each_region_plus_global(client, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))

    client.post("/refresh", data={"regions": ["AMER", "APAC"]})
    page = client.get("/", params=[("regions", "AMER"), ("regions", "APAC")]).text

    # Both region group headers render.
    assert ">AMER " in page or ">AMER<" in page
    assert "APAC" in page
    # Fernando (AMER+APAC manager) appears once under each selected region → twice.
    assert page.count("Fernando Carrillo Castro") >= 2
    # Global group with its managers is shown alongside.
    assert "Global" in page
    assert "Kristofer Tingdahl" in page
