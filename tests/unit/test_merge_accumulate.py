"""load_merged_data accumulates state across the pulse's fetch layers (#88)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    CalendarAvail,
    Pulse,
    Ticket,
    TouchEvent,
    TouchKind,
)
from standup_dashboard.services.counts import (
    accumulated_alerts_since,
    accumulated_pulse_alerts,
)
from standup_dashboard.storage.db import Database
from standup_dashboard.web import presenters

E = "alexandre.gomes@canonical.com"


def _dt(d, h=12):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def test_calendar_merges_per_engineer_across_fetches(tmp_path, db_dsn):
    """A later refresh that transiently drops one engineer's iCal feed must keep
    that engineer's last-good calendar, not blank them (#cal)."""
    db = Database(db_dsn)
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


def test_partial_snapshot_merges_but_does_not_anchor_jira_window(tmp_path, db_dsn):
    """A single-person partial refresh (#person-refresh): its tickets still merge into
    the view, but it must NOT become the Jira incremental anchor — otherwise the next
    global fetch would resume from one person's data and miss everyone else."""
    db = Database(db_dsn)
    full = db.create_fetch_snapshot(_dt(10), True, True, True, "")
    db.insert_tickets(full, [Ticket("ISReq-1", "ISReq", "a", "To Do", None)])
    # A later partial refresh (jira_ok True, but partial) adds one person's ticket.
    part = db.create_fetch_snapshot(_dt(12), True, True, True, "", partial=True)
    db.insert_tickets(part, [Ticket("ISDB-9", "ISDB", "b", "In Progress", None)])

    # Latest snapshot is the partial one, but the Jira anchor skips it.
    assert db.latest_fetch().id == part
    assert db.latest_good_fetch().id == full        # window stays on the full fetch
    # Both fetches' tickets still merge into the pulse view.
    data = presenters.load_merged_data(db, _dt(12))
    assert {t.id for t in data.tickets} == {"ISReq-1", "ISDB-9"}
    db.close()


def test_merge_accumulates_across_fetches(tmp_path, db_dsn):
    db = Database(db_dsn)
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


def test_open_ack_persists_across_pulse_rollover_but_resolved_does_not(tmp_path, db_dsn):
    """An incident still acked-but-unresolved is ongoing work, so it stays on the
    cards after the pulse rolls — while a prior-pulse *resolved* incident is gone
    (#open-alert-persist / #stale-prev-pulse). now=Jun 23 → pulse starts Jun 22."""
    db = Database(db_dsn)
    # A post-rollover fetch (Jun 23). The recheck re-emits the still-open incident's
    # original ACK (timestamped Jun 20, before the pulse) alongside this pulse's work.
    f = db.create_fetch_snapshot(_dt(23), True, True, True, "")
    db.insert_pulses(f, [Pulse("ISReq", 203, "s", _dt(22), _dt(30))])
    db.insert_alerts(f, [
        Alert("INC-OPEN", E, AlertState.ACKNOWLEDGED, _dt(20)),    # last pulse, still open
        Alert("INC-CLOSED", E, AlertState.ACKNOWLEDGED, _dt(19)),  # last pulse, resolved
        Alert("INC-CLOSED", E, AlertState.RESOLVED, _dt(20)),
        Alert("INC-NEW", E, AlertState.ACKNOWLEDGED, _dt(23)),     # this pulse
    ])

    merged = presenters.load_merged_data(db, _dt(23, 18))
    ids = {a.id for a in merged.alerts}
    assert "INC-OPEN" in ids        # open ACK survives the rollover
    assert "INC-NEW" in ids
    assert "INC-CLOSED" not in ids  # resolved prior-pulse alert stays dropped
    assert {a.state for a in merged.alerts if a.id == "INC-OPEN"} == {
        AlertState.ACKNOWLEDGED}
    db.close()


def test_open_ack_drops_to_resolved_state_once_it_resolves_after_rollover(tmp_path, db_dsn):
    """Once the lingering open incident finally resolves this pulse it reads as
    RESOLVED (not a stale ACK): the pre-pulse ACK is dropped, the resolve shows."""
    db = Database(db_dsn)
    f = db.create_fetch_snapshot(_dt(23), True, True, True, "")
    db.insert_alerts(f, [
        Alert("INC-OPEN", E, AlertState.ACKNOWLEDGED, _dt(20)),  # acked last pulse
        Alert("INC-OPEN", E, AlertState.RESOLVED, _dt(23)),      # resolved this pulse
    ])
    merged = presenters.load_merged_data(db, _dt(23, 18))
    assert {a.state for a in merged.alerts if a.id == "INC-OPEN"} == {
        AlertState.RESOLVED}
    db.close()


def test_open_ack_recheck_lookback_spans_a_prior_pulse(tmp_path, db_dsn):
    """The recheck pool must look back past pulse start (OPEN_ALERT_RECHECK_DAYS) so
    an incident acked last pulse keeps being polled — the pulse-scoped set misses
    it, which is exactly why it would otherwise stop being fetched at rollover."""
    db = Database(db_dsn)
    # A prior-pulse fetch (Jun 20) holding one still-open and one resolved incident.
    f = db.create_fetch_snapshot(_dt(20), True, True, True, "")
    db.insert_alerts(f, [
        Alert("INC-OPEN", E, AlertState.ACKNOWLEDGED, _dt(20)),
        Alert("INC-DONE", E, AlertState.ACKNOWLEDGED, _dt(19)),
        Alert("INC-DONE", E, AlertState.RESOLVED, _dt(19, 13)),
    ])
    now = _dt(23, 18)
    wide = accumulated_alerts_since(db, now - timedelta(days=30))
    open_ids = ({a.id for a in wide if a.state is AlertState.ACKNOWLEDGED}
                - {a.id for a in wide if a.state is AlertState.RESOLVED})
    assert open_ids == {"INC-OPEN"}
    # The pulse-scoped accumulation (since Jun 22) can't see it — hence the wider pool.
    assert "INC-OPEN" not in {a.id for a in accumulated_pulse_alerts(db, now)}
    db.close()
