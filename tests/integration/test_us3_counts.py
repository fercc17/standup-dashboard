"""US3 integration: counts table renders rows/columns with mocked data (T041)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fixtures.jira_pd import Scenario, install, issue

MEMBER = "alexandre.gomes@canonical.com"  # AMER


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=4), now + timedelta(days=4)
    acked = _jira_dt(now - timedelta(hours=2))
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
            201: [issue("ISReq-1", assignee=MEMBER, status="In Progress",
                        priority="Highest", sprint_id=201)],
        },
        users=[{"id": "PU1", "email": MEMBER, "name": "Alexandre Gomes"}],
        incidents=[{"id": "INC1"}],
        log_entries={
            "INC1": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                      "created_at": acked}],
        },
    )


def test_us3_counts_table_renders(client, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))

    client.post("/refresh", data={"regions": "AMER"})
    page = client.get("/", params={"regions": "AMER"}).text

    assert "Pulse counts" in page
    assert "<table class=\"counts\">" in page
    # Metric columns present (headers are stacked two-line spans).
    for header in ("Highest", "ISDB", "ps5", "PR/MP", "Review",
                   "Ack", "Res", "Total", "Region %"):
        assert header in page
    assert "Pulse total" in page  # the pulse summary row
    # Today's row carries the open-Highest snapshot.
    assert "100%" in page  # region share of its own alert that day
