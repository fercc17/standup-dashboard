"""Growing per-pulse history with per-person tooltips + attribution (#80)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, Pulse, Ticket
from standup_dashboard.services import counts
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import DashboardData, build_pulse_history

MEMBER = "alexandre.gomes@canonical.com"   # AMER → assignee
REQ = "jane.doe@external.com"              # requestor → "Jane Doe"


def utc(d, h=18):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def _data():
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    tickets = [
        Ticket("ISReq-H", "ISReq", "x", "To Do", "Highest",
               assignee_email=MEMBER, reporter_email=REQ, created=utc(12)),
        Ticket("ISReq-PR", "ISReq", "[PR/MP Review] y", "To Do", "Medium",
               assignee_email=MEMBER, reporter_email=REQ, created=utc(12)),
        Ticket("ISReq-C", "ISReq", "z", "Done", "Highest",
               assignee_email=MEMBER, is_done_date=date(2026, 6, 12)),
    ]
    alerts = [Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12))]
    return DashboardData(fetched_at=utc(12), tickets=tickets, alerts=alerts, pulses=pulses)


def test_history_attribution_requestor_vs_assignee(tmp_path):
    db = Database(tmp_path / "t.db")
    data = _data()
    rows = build_pulse_history(db, data, ["AMER"], utc(12, 19))
    cur = {r.pulse_number: r for r in rows}[12].cells
    # New Highest breaks down by requestor (reporter); PR/MP by assignee.
    assert cur["new_highest"].count == 1
    assert cur["new_highest"].breakdown == {"Jane Doe": 1}
    assert cur["new_pr_mp"].breakdown == {"Alexandre Gomes": 1}
    assert cur["new_total"].breakdown == {"Jane Doe": 2}        # both new, by requestor
    # Closed breaks down by assignee.
    assert cur["closed_total"].breakdown == {"Alexandre Gomes": 1}
    # Only-AMER alert/closed → AMER is 100% of all alerts and closes that pulse.
    cur_row = {r.pulse_number: r for r in rows}[12]
    assert cur_row.region_pct == 100.0
    assert cur_row.closed_pct == 100.0
    db.close()


def test_history_persists_counts_and_breakdowns(tmp_path):
    db = Database(tmp_path / "t.db")
    data = _data()
    counts.persist_pulse_summaries(db, data.tickets, [], data.pulses, utc(12))
    stored = {(p, r): (c, b) for p, r, c, b in db.get_pulse_summaries()}
    c12, b12 = stored[(12, "AMER")]
    assert c12["new_total"] == 2 and c12["closed_total"] == 1
    assert b12["new_highest"] == {"Jane Doe": 1}        # requestor persisted
    assert b12["closed_total"] == {"Alexandre Gomes": 1}
    assert (11, "AMER") in stored                        # previous pulse stored too
    db.close()
