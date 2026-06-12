"""Two-row chip metrics: last 24h vs since pulse start (#chip-metrics)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    Pulse,
    Role,
    Ticket,
    TouchEvent,
    TouchKind,
)
from standup_dashboard.web.presenters import DashboardData, build_chip

E = "alexandre.gomes@canonical.com"


def utc(d, h=12):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def test_build_chip_24h_and_pulse_windows():
    now = utc(12)  # pulse 12 started Mon Jun 8
    data = DashboardData(
        fetched_at=now,
        tickets=[
            Ticket(id="ISReq-1", project_key="ISReq", title="x", status="In Progress",
                   priority=None, labels=[], assignee_email=E, sprint_id=201),
            Ticket(id="ISReq-2", project_key="ISReq", title="x", status="Done",
                   priority=None, labels=[], assignee_email=E, sprint_id=201,
                   is_done_date=date(2026, 6, 12)),
            Ticket(id="ISReq-3", project_key="ISReq", title="x", status="Done",
                   priority=None, labels=[], assignee_email=E, sprint_id=201,
                   is_done_date=date(2026, 6, 9)),
        ],
        touches=[
            TouchEvent("ISReq-1", E, TouchKind.COMMENT, utc(12, 10)),
            TouchEvent("ISReq-9", E, TouchKind.COMMENT, utc(9)),
        ],
        alerts=[
            Alert("INC1", E, AlertState.ACKNOWLEDGED, utc(12, 10)),
            Alert("INC2", E, AlertState.RESOLVED, utc(9)),
        ],
        pulses=[Pulse("ISReq", 201, "s", utc(8), utc(20))],
    )
    chip = build_chip(E, Role.GEN, "AMER", data, now)
    assert chip.assigned_open == 1               # only the open In Progress ticket
    assert chip.completed_24h == 1               # ISReq-2 done today
    assert chip.completed_pulse == 2             # ISReq-2 + ISReq-3 since Jun 8
    assert chip.touched_24h == 1                 # only today's touch
    assert chip.touched_pulse == 2               # both touches this pulse
    assert (chip.alerts_ack_24h, chip.alerts_resolved_24h) == (1, 0)
    assert (chip.alerts_ack_pulse, chip.alerts_resolved_pulse) == (1, 1)
