"""US3 unit tests: per-day bucketing, nine columns, weekend-combine (T036)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from standup_dashboard.domain.models import Alert, AlertState, Pulse, Ticket
from standup_dashboard.services.counts import build_region_counts

AMER = "AMER"
MEMBER = "alexandre.gomes@canonical.com"


def utc(y, m, d, h=18):
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def _pulses():
    # Noon UTC ≈ 06:00 in Mexico City, so the region-local day equals the date.
    start, end = utc(2026, 6, 11, 12), utc(2026, 6, 19, 12)
    return [Pulse("ISDB", 101, "s", start, end), Pulse("ISReq", 201, "s", start, end)]


def _build(now):
    tickets = [
        Ticket(id="ISDB-1", project_key="ISDB", title="x", status="Done", priority=None,
               labels=[], is_done_date=date(2026, 6, 12)),
        Ticket(id="ISReq-1", project_key="ISReq", title="x", status="In Progress",
               priority="Highest", labels=[], created=now - timedelta(hours=2)),
    ]
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(2026, 6, 13)),   # Saturday
        Alert("INC2", MEMBER, AlertState.RESOLVED, utc(2026, 6, 14)),       # Sunday
    ]
    return build_region_counts(AMER, tickets, alerts, _pulses(), now)


def test_one_row_per_day_with_weekend_combined():
    now = utc(2026, 6, 15)  # Monday
    rows = _build(now)
    # Thu, Fri, Weekend(Sat+Sun), Mon
    assert len(rows) == 4
    assert rows[0].label.startswith("Thu")
    assert rows[1].label.startswith("Fri")
    assert rows[2].is_weekend and "Sat–Sun" in rows[2].label
    assert rows[3].label.startswith("Mon")


def test_weekend_row_combines_saturday_and_sunday_alerts():
    rows = _build(utc(2026, 6, 15))
    weekend = rows[2]
    assert weekend.alerts_ack == 1
    assert weekend.alerts_resolved == 1
    assert weekend.alerts_total == 2
    # Only-region alerts → region is 100% of the global total that weekend.
    assert weekend.region_alert_pct == 100.0


def test_isdb_completed_buckets_to_its_day():
    rows = _build(utc(2026, 6, 15))
    assert rows[1].isdb_completed == 1   # Friday
    assert rows[0].isdb_completed == 0   # Thursday


def test_snapshot_and_24h_columns_on_today_row_only():
    rows = _build(utc(2026, 6, 15))
    today = rows[3]  # Monday = today
    assert today.open_highest_isreq == 1
    assert today.new_highest_isreq_24h == 1
    # Earlier rows carry no snapshot figure.
    assert rows[0].open_highest_isreq == 0
    assert rows[0].new_highest_isreq_24h == 0


def test_days_capped_at_today():
    # Today is the Thursday the pulse starts → a single row.
    rows = _build(utc(2026, 6, 11))
    assert len(rows) == 1
    assert rows[0].label.startswith("Thu")
