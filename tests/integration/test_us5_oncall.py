"""US5 integration: Monday combined-weekend view from an iCal fixture (T051).

Drives ``run_fetch`` directly with a fixed Monday ``now`` (the HTTP route can't
control the clock), then asserts on-call resolution + the combined weekend row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.services.counts import build_counts
from standup_dashboard.services.fetch import run_fetch
from standup_dashboard.web import presenters
from tests.fixtures.jira_pd import Scenario, install

FERNANDO = "fernando.carrillo.castro@canonical.com"  # AMER member

ICAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART;VALUE=DATE:20260613
DTEND;VALUE=DATE:20260615
SUMMARY:On-call: Fernando Carrillo Castro
END:VEVENT
END:VCALENDAR
"""


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=UTC)


def _scenario() -> Scenario:
    start, end = _utc(2026, 6, 10, 12), _utc(2026, 6, 19, 12)
    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={101: [], 201: []},
        users=[{"id": "PU1", "email": FERNANDO, "name": "Fernando Carrillo Castro"}],
        incidents=[{"id": "INC1"}, {"id": "INC2"}],
        log_entries={
            "INC1": [{"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
                      "created_at": _jira_dt(_utc(2026, 6, 13, 18))}],   # Saturday
            "INC2": [{"type": "resolve_log_entry", "agent": {"id": "PU1"},
                      "created_at": _jira_dt(_utc(2026, 6, 14, 18))}],   # Sunday
        },
        ical_text=ICAL,
    )


async def test_us5_monday_combined_weekend(app, respx_mock):
    install(respx_mock, _scenario())
    ctx = app.state.ctx
    now = _utc(2026, 6, 15, 18)  # Monday

    fetch_id = await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=now)

    # The single weekend on-call is resolved from the iCal feed and stored.
    oncall = ctx.db.get_weekend_oncall(fetch_id)
    assert len(oncall) == 1 and oncall[0].engineer_email == FERNANDO

    # The Monday weekend row combines Saturday + Sunday activity.
    data = presenters.load_fetch_data(ctx.db, now, fetch_id)
    rows = build_counts(["AMER"], data.tickets, data.alerts, data.pulses, now)
    weekend = [r for r in rows if r.is_weekend]
    assert len(weekend) == 1
    assert weekend[0].alerts_ack == 1
    assert weekend[0].alerts_resolved == 1
    assert weekend[0].alerts_total == 2
