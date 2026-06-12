"""US4 unit tests: cross-region dedup, manager-once, Global exclusion (T042)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.models import Alert, AlertState, Pulse, Ticket
from standup_dashboard.services.counts import build_counts

FERNANDO = "fernando.carrillo.castro@canonical.com"  # AMER + APAC manager
JAMES = "james.simpson@canonical.com"                # APAC
BENJAMIN = "benjamin.allot@canonical.com"            # EMEA
KRISTOFER = "kristofer.tingdahl@canonical.com"       # Global


def utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=UTC)


def _pulses(now):
    # A single Monday (2026-06-15) bucketed identically in AMER/APAC/EMEA at 10:00 UTC.
    start = utc(2026, 6, 15, 8)
    return [Pulse("ISDB", 101, "s", start, start), Pulse("ISReq", 201, "s", start, start)]


def _rows(selected):
    now = utc(2026, 6, 15, 12)
    at = utc(2026, 6, 15, 10)  # date 2026-06-15 in AMER, APAC and EMEA tz
    tickets = [Ticket(id="ISReq-1", project_key="ISReq", title="x", status="In Progress",
                      priority="Highest", labels=[], created=now - timedelta(hours=1))]
    alerts = [
        Alert("INC1", FERNANDO, AlertState.ACKNOWLEDGED, at),  # same incident,
        Alert("INC1", JAMES, AlertState.ACKNOWLEDGED, at),     # two handlers → dedup
        Alert("INC9", FERNANDO, AlertState.ACKNOWLEDGED, at),  # manager's own
        Alert("INC2", BENJAMIN, AlertState.ACKNOWLEDGED, at),  # EMEA (not selected)
        Alert("INC3", KRISTOFER, AlertState.ACKNOWLEDGED, at),  # Global → excluded
    ]
    return build_counts(selected, tickets, alerts, _pulses(now), now)


def test_alert_dedup_and_manager_counted_once():
    rows = _rows(["AMER", "APAC"])
    assert len(rows) == 1
    row = rows[0]
    # INC1 (two handlers) deduped to one; INC9 once → ack = 2, not 3.
    assert row.alerts_ack == 2
    assert row.alerts_total == 2


def test_denominator_excludes_global_and_uses_three_region_total():
    row = _rows(["AMER", "APAC"])[0]
    # Region acked {INC1, INC9} = 2; global non-Global total {INC1, INC9, INC2} = 3.
    assert row.region_alert_pct is not None
    assert round(row.region_alert_pct) == 67


def test_tickets_not_double_counted_across_regions():
    one = _rows(["AMER"])[0]
    two = _rows(["AMER", "APAC"])[0]
    # Ticket columns are project-wide; selecting more regions doesn't multiply them.
    assert one.open_highest_isreq == 1
    assert two.open_highest_isreq == 1
