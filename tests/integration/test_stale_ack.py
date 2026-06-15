"""PVG handled alerts are state+age based (#158): an open ack ≤24h is yellow,
an open ack >24h is red (resolved would be green)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.services.fetch import run_fetch
from standup_dashboard.web import presenters
from tests.fixtures.jira_pd import Scenario, install

PVG = "alexandre.gomes@canonical.com"  # AMER
AMER_TZ = "America/Mexico_City"


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
        incidents=[
            {"id": "INC-NEW", "title": "recent alert", "incident_number": 1},
            {"id": "INC-OLD", "title": "stale alert", "incident_number": 2},
        ],
        log_entries={
            "INC-NEW": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                         "created_at": recent}],
            "INC-OLD": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                         "created_at": stale}],
        },
    )


async def test_pvg_open_alerts_yellow_then_red_by_age(app, respx_mock):
    now = _utc(2026, 6, 12, 18)
    install(respx_mock, _scenario(now))
    ctx = app.state.ctx
    # PVG keeps open alerts in WIP, coloured by age (#158): ≤24h yellow, >24h red.
    ctx.db.set_weekly_role(PVG, region_weekday(now, AMER_TZ), "PVG", now)
    fetch_id = await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=now, window_days=3)
    data = presenters.load_fetch_data(ctx.db, now, fetch_id)
    panel = presenters.build_panel(ctx.db, PVG, data, now, region_key="AMER")
    wip = panel.groups["WIP"]
    recent = next(t for t in wip if "recent alert" in t.title)
    stale = next(t for t in wip if "stale alert" in t.title)
    assert recent.color.value == "yellow"       # PVG open ≤24h → yellow
    assert stale.color.value == "red"           # PVG open >24h → red
    assert recent.title.startswith("ACK")       # line still starts with the status
