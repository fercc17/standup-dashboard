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
            # New ISReq Highest ticket reported by the member (counts are ISReq, #91).
            201: [issue("ISReq-1", assignee=MEMBER, reporter=MEMBER, status="To Do",
                        priority="Highest", sprint_id=201, created=_jira_dt(now))],
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

    import re
    assert re.search(r"Pulse \d+ counts", page)  # title carries the pulse number
    assert "<table class=\"counts\">" in page
    # Redesigned columns: ISReq new/closed groups + relabelled alert columns (#91).
    for header in ("New", "Highest", "PR/MP", "Review", "ps5", "Regular",
                   "Closed", "Alerts", "Ack", "Res", "Region %"):
        assert header in page
    assert "Pulse total" in page  # the pulse summary row
    assert "Pulse history" in page  # growing per-pulse history table (#80)
    # New ISReq Highest ticket reported by the member → tooltip breaks down by person.
    assert "Alexandre Gomes ×1" in page
    # Today's row: region share of its own alert that day.
    assert "100%" in page
