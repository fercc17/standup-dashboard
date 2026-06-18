"""load_merged_data accumulates state across the pulse's fetch layers (#88)."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    CalendarAvail,
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


def test_calendar_merges_per_engineer_across_fetches(tmp_path):
    """A later refresh that transiently drops one engineer's iCal feed must keep
    that engineer's last-good calendar, not blank them (#cal)."""
    db = Database(tmp_path / "t.db")
    pulse = [Pulse("ISReq", 202, "s", _dt(8), _dt(20))]
    fer, jam = "fernando.carrillo.castro@canonical.com", "james.simpson@canonical.com"

    # Fetch 1: both engineers' calendars came back.
    f1 = db.create_fetch_snapshot(_dt(10), True, True, True, "")
    db.insert_pulses(f1, pulse)
    db.insert_calendar_avail(f1, {
        fer: CalendarAvail(busy_seconds=3600, has_data=True, busy_today_seconds=1800),
        jam: CalendarAvail(busy_seconds=7200, has_data=True, busy_today_seconds=600),
    })

    # Fetch 2: James's feed timed out, only Fernando's returned (with a new value).
    f2 = db.create_fetch_snapshot(_dt(12), True, True, True, "")
    db.insert_pulses(f2, pulse)
    db.insert_calendar_avail(f2, {
        fer: CalendarAvail(busy_seconds=5400, has_data=True, busy_today_seconds=2400),
    })

    merged = presenters.load_merged_data(db, _dt(12, 18))
    # Fernando updates to the latest fetch; James survives from fetch 1.
    assert merged.calendar[fer].busy_today_seconds == 2400
    assert merged.calendar[jam].busy_today_seconds == 600
    db.close()


def test_merge_accumulates_across_fetches(tmp_path):
    db = Database(tmp_path / "t.db")
    pulse = [Pulse("ISReq", 201, "s", _dt(8), _dt(20))]

    # Fetch 1 (Jun 10): ISReq-1, a touch, INC1 acked, and INC2 acked (no title yet).
    f1 = db.create_fetch_snapshot(_dt(10), True, True, True, "")
    db.insert_tickets(f1, [Ticket("ISReq-1", "ISReq", "a", "To Do", None)])
    db.insert_touches(f1, [TouchEvent("ISReq-1", E, TouchKind.COMMENT, _dt(10))])
    db.insert_alerts(f1, [
        Alert("INC1", E, AlertState.ACKNOWLEDGED, _dt(10)),
        Alert("INC2", E, AlertState.ACKNOWLEDGED, _dt(10)),
    ])
    db.insert_pulses(f1, pulse)

    # Fetch 2 (Jun 12, incremental): only ISReq-2 + its touch; INC1 now resolved.
    # The 1h overlap re-emits INC2's ACK, this time enriched with an incident title.
    f2 = db.create_fetch_snapshot(_dt(12), True, True, True, "")
    db.insert_tickets(f2, [Ticket("ISReq-2", "ISReq", "b", "In Progress", None)])
    db.insert_touches(f2, [TouchEvent("ISReq-2", E, TouchKind.COMMENT, _dt(12))])
    db.insert_alerts(f2, [
        Alert("INC1", E, AlertState.RESOLVED, _dt(12)),
        Alert("INC2", E, AlertState.ACKNOWLEDGED, _dt(10), title="DB down"),
    ])
    db.insert_pulses(f2, pulse)

    merged = presenters.load_merged_data(db, _dt(12, 18))
    # Tickets/touches accumulate: the delta fetch dropped ISReq-1, merge keeps it.
    assert {t.id for t in merged.tickets} == {"ISReq-1", "ISReq-2"}
    assert len(merged.touches) == 2
    # Alerts accumulate too (PD is now fetched incrementally): INC1's ACK (fetch 1)
    # and RESOLVED (fetch 2) both survive — the delta fetch never re-sent the ACK.
    assert {a.state for a in merged.alerts if a.id == "INC1"} == {
        AlertState.ACKNOWLEDGED, AlertState.RESOLVED}
    # INC2's re-emitted ACK is deduped to one row, keeping the enriched (titled) copy.
    inc2 = [a for a in merged.alerts if a.id == "INC2"]
    assert len(inc2) == 1 and inc2[0].title == "DB down"
    assert len(merged.alerts) == 3
    db.close()
