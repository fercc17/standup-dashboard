"""US4 unit tests: cross-region dedup, management exclusion from counts (T042, #72)."""

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
                      priority="Highest", labels=[], created=now - timedelta(hours=1),
                      reporter_email=JAMES)]
    alerts = [
        Alert("INC1", FERNANDO, AlertState.ACKNOWLEDGED, at),  # manager → excluded,
        Alert("INC1", JAMES, AlertState.ACKNOWLEDGED, at),     # but James (APAC) counts it
        Alert("INC9", FERNANDO, AlertState.ACKNOWLEDGED, at),  # manager-only → excluded
        Alert("INC2", BENJAMIN, AlertState.ACKNOWLEDGED, at),  # EMEA (not selected)
        Alert("INC3", KRISTOFER, AlertState.ACKNOWLEDGED, at),  # Global → excluded
    ]
    return build_counts(selected, tickets, alerts, _pulses(now), now)


def test_alert_dedup_and_managers_excluded_from_counts():
    rows = _rows(["AMER", "APAC"])
    day_rows = [r for r in rows if not r.is_total]
    assert len(day_rows) == 1
    row = day_rows[0]
    # INC1 has two handlers — Fernando (manager, excluded) + James (APAC). It is
    # counted once via James. INC9 is Fernando-only (manager) → excluded (#72).
    assert row.alerts_ack.count == 1
    assert row.alerts_total.count == 1


def test_denominator_excludes_management_and_uses_three_region_total():
    row = _rows(["AMER", "APAC"])[0]
    # Region acked {INC1} = 1; counted (non-management) total {INC1, INC2} = 2.
    assert row.region_alert_pct is not None
    assert round(row.region_alert_pct) == 50


def test_tickets_not_double_counted_across_regions():
    one = _rows(["AMER"])[0]
    two = _rows(["AMER", "APAC"])[0]
    # Ticket columns are project-wide; selecting more regions doesn't multiply them.
    assert one.new_highest.count == 1
    assert two.new_highest.count == 1
