"""US6 integration: history retained, per-source flags, last-good fallback (T052).

Two fetches are run with a fixed clock; the second simulates a Jira outage. We
assert retention (nothing deleted), per-source ok flags, last-good fallback in
the UI, and that no external write was ever issued (read-only, FR-027).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from standup_dashboard.services.fetch import run_fetch
from tests.fixtures.jira_pd import Scenario, install, issue

MEMBER = "alexandre.gomes@canonical.com"  # AMER


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
            101: [issue("ISDB-1", assignee=MEMBER, status="In Progress", sprint_id=101)],
            201: [],
        },
        users=[{"id": "PU1", "email": MEMBER, "name": "Alexandre Gomes"}],
    )


async def test_us6_history_retention_and_fallback(app, client, tmp_path, respx_mock):
    ctx = app.state.ctx
    scenario = _scenario(datetime.now(UTC))
    install(respx_mock, scenario)

    t1 = datetime(2026, 6, 11, 12, tzinfo=UTC)
    t2 = datetime(2026, 6, 11, 13, tzinfo=UTC)

    fetch1 = await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=t1)
    assert ctx.db.get_tickets(fetch1)  # Jira data captured

    # Simulate a Jira outage on the second fetch.
    scenario.fail_jira = True
    fetch2 = await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=t2)

    # Both snapshots retained; the earlier fetch's data is untouched (no delete).
    assert ctx.db.count_fetch_snapshots() == 2
    assert ctx.db.get_tickets(fetch1), "earlier fetch data must persist"

    # Per-source flags: fetch2 marks Jira down but PagerDuty still ok.
    f2 = ctx.db.latest_fetch()
    assert f2.id == fetch2 and f2.jira_ok is False and f2.pagerduty_ok is True
    assert ctx.db.latest_good_fetch().id == fetch1

    # Two raw snapshot directories exist on disk.
    snap_dirs = list((tmp_path / "snapshots").iterdir())
    assert len(snap_dirs) == 2 and all(Path(d).is_dir() for d in snap_dirs)

    # UI falls back to last good data with a failure banner.
    page = client.get("/", params={"regions": "AMER"}).text
    assert "Latest refresh failed" in page
    assert "Alexandre Gomes" in page  # last-good chip still rendered

    # Read-only: every external call issued was a GET (FR-027).
    assert respx_mock.calls.call_count > 0
    for call in respx_mock.calls:
        assert call.request.method == "GET"
