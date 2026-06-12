"""Acked alerts: yellow within 24h, red once stale (>24h, unresolved)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.services.fetch import run_fetch
from standup_dashboard.web import presenters
from tests.fixtures.jira_pd import Scenario, install

PVG = "alexandre.gomes@canonical.com"  # AMER


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=UTC)


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=5), now + timedelta(days=5)
    recent = _jira_dt(now - timedelta(hours=2))    # acked 2h ago → yellow
    stale = _jira_dt(now - timedelta(hours=40))    # acked 40h ago, unresolved → red
    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={101: [], 201: []},
        users=[{"id": "PU1", "email": PVG, "name": "Alexandre Gomes"}],
        incidents=[{"id": "INC-NEW"}, {"id": "INC-OLD"}],
        log_entries={
            "INC-NEW": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                         "created_at": recent}],
            "INC-OLD": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                         "created_at": stale}],
        },
    )


async def test_stale_ack_is_red_recent_ack_is_yellow(app, respx_mock):
    now = _utc(2026, 6, 12, 18)
    install(respx_mock, _scenario(now))
    ctx = app.state.ctx
    fetch_id = await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=now, window_days=3)
    data = presenters.load_fetch_data(ctx.db, now, fetch_id)
    panel = presenters.build_panel(ctx.db, PVG, data, now, region_key="AMER")
    wip = {t.key: t.color.value for t in panel.groups["WIP"]}
    assert wip["⚠ INC-NEW"] == "yellow"   # acked 2h ago
    assert wip["⚠ INC-OLD"] == "red"      # acked 40h ago, still not resolved
