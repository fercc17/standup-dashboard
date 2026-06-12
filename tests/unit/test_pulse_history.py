"""Growing per-pulse history table (#80): persist + build."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Pulse, Ticket
from standup_dashboard.services import counts
from standup_dashboard.storage.db import Database
from standup_dashboard.web import presenters

MEMBER = "alexandre.gomes@canonical.com"  # AMER


def utc(d, h=18):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def test_persist_then_build_history(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    tickets = [
        Ticket("ISReq-1", "ISReq", "x", "Done", None,
               assignee_email=MEMBER, is_done_date=date(2026, 6, 12)),
    ]
    now = utc(12)

    counts.persist_pulse_summaries(db, tickets, [], pulses, now)
    summaries = {(p, r): m for p, r, m in db.get_pulse_summaries()}
    assert summaries[(12, "AMER")]["closed_total"] == 1   # current pulse, AMER
    assert (11, "AMER") in summaries                       # previous pulse stored too

    rows = presenters.build_pulse_history(db, [], ["AMER"], now)
    by_num = {r.pulse_number: r for r in rows}
    assert by_num[12].closed_total == 1
    assert 11 in by_num                                    # history includes prior pulse
    db.close()
