"""load_merged_data accumulates state across the pulse's fetch layers (#88)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    Pulse,
    Ticket,
    TouchEvent,
    TouchKind,
)
from standup_dashboard.storage.db import Database
from standup_dashboard.web import presenters

E = "alexandre.gomes@canonical.com"


def _dt(d, h=12):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def test_merge_accumulates_across_fetches(tmp_path):
    db = Database(tmp_path / "t.db")
    pulse = [Pulse("ISReq", 201, "s", _dt(8), _dt(20))]

    # Fetch 1 (Jun 10): ISReq-1, a touch, and an acked alert.
    f1 = db.create_fetch_snapshot(_dt(10), True, True, True, "")
    db.insert_tickets(f1, [Ticket("ISReq-1", "ISReq", "a", "To Do", None)])
    db.insert_touches(f1, [TouchEvent("ISReq-1", E, TouchKind.COMMENT, _dt(10))])
    db.insert_alerts(f1, [Alert("INC1", E, AlertState.ACKNOWLEDGED, _dt(10))])
    db.insert_pulses(f1, pulse)

    # Fetch 2 (Jun 12, incremental): only ISReq-2 + its touch; INC1 now resolved.
    f2 = db.create_fetch_snapshot(_dt(12), True, True, True, "")
    db.insert_tickets(f2, [Ticket("ISReq-2", "ISReq", "b", "In Progress", None)])
    db.insert_touches(f2, [TouchEvent("ISReq-2", E, TouchKind.COMMENT, _dt(12))])
    db.insert_alerts(f2, [Alert("INC1", E, AlertState.RESOLVED, _dt(12))])
    db.insert_pulses(f2, pulse)

    merged = presenters.load_merged_data(db, _dt(12, 18))
    # Tickets/touches accumulate: the delta fetch dropped ISReq-1, merge keeps it.
    assert {t.id for t in merged.tickets} == {"ISReq-1", "ISReq-2"}
    assert len(merged.touches) == 2
    # Alerts come from the latest successful fetch only (PD is re-fetched in full),
    # so the older ACK doesn't linger — only fetch 2's RESOLVED is present.
    assert {a.state for a in merged.alerts} == {AlertState.RESOLVED}
    db.close()
