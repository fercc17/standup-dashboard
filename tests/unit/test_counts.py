"""US3 + #91 unit tests: ISReq new/closed buckets, per-person tooltips, weekend."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, Pulse, Ticket
from standup_dashboard.services.counts import build_region_counts

AMER = "AMER"
MEMBER = "alexandre.gomes@canonical.com"   # roster → "Alexandre Gomes"
OTHER = "casey.partner@example.com"        # non-roster → derived "Casey Partner"


def utc(y, m, d, h=18):
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def _pulses():
    # 18:00 UTC ≈ 12:00 in Mexico City, so the region-local day equals the date.
    # Within the anchored pulse 12 (Jun 8–21), so the window is the sprint span.
    start, end = utc(2026, 6, 11), utc(2026, 6, 19)
    return [Pulse("ISDB", 101, "s", start, end), Pulse("ISReq", 201, "s", start, end)]


def _build(now):
    fri = utc(2026, 6, 12)  # a pulse weekday (Friday)
    tickets = [
        # New ISReq tickets created Friday — one per bucket.
        Ticket(id="ISReq-H", project_key="ISReq", title="boom", status="To Do",
               priority="Highest", labels=[], created=fri, reporter_email=MEMBER),
        Ticket(id="ISReq-PR", project_key="ISReq", title="[PR/MP Review] x", status="To Do",
               priority="Medium", labels=[], created=fri, reporter_email=MEMBER),
        Ticket(id="ISReq-P5", project_key="ISReq", title="blk", status="To Do",
               priority="Medium", labels=["ps5-blocker"], created=fri, reporter_email=OTHER),
        Ticket(id="ISReq-R", project_key="ISReq", title="reg", status="To Do",
               priority="Medium", labels=[], created=fri, reporter_email=MEMBER),
        # Closed ISReq Highest on Friday.
        Ticket(id="ISReq-C", project_key="ISReq", title="done", status="Done",
               priority="Highest", labels=[], is_done_date=date(2026, 6, 12),
               assignee_email=MEMBER),
        # Non-ISReq (ISDB) ticket is ignored by the counts table.
        Ticket(id="ISDB-1", project_key="ISDB", title="x", status="To Do",
               priority="Highest", labels=[], created=fri, reporter_email=MEMBER),
    ]
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(2026, 6, 13)),   # Saturday
        Alert("INC2", MEMBER, AlertState.RESOLVED, utc(2026, 6, 14)),       # Sunday
    ]
    return build_region_counts(AMER, tickets, alerts, _pulses(), now)


def test_one_row_per_day_with_weekend_combined():
    rows = _build(utc(2026, 6, 15))  # Monday
    day_rows = [r for r in rows if not r.is_total]
    assert len(day_rows) == 4
    assert rows[-1].is_total and rows[-1].label == "Pulse total"
    assert rows[0].label.startswith("Thu")
    assert rows[1].label.startswith("Fri")
    assert rows[2].is_weekend and "Sat–Sun" in rows[2].label
    assert rows[3].label.startswith("Mon")


def test_new_isreq_buckets_sum_to_total_on_created_day():
    rows = _build(utc(2026, 6, 15))
    fri = rows[1]
    assert fri.new_highest.count == 1
    assert fri.new_pr_mp.count == 1
    assert fri.new_ps5.count == 1
    assert fri.new_regular.count == 1
    assert fri.new_total.count == 4            # ISDB excluded
    assert rows[0].new_total.count == 0        # Thursday has no new ISReq tickets


def test_new_bucket_precedence_highest_wins():
    fri = utc(2026, 6, 12)
    # Highest + [PR/MP Review] + ps5 → counted once, in the Highest bucket only.
    t = Ticket(id="ISReq-X", project_key="ISReq", title="[PR/MP Review] hot", status="To Do",
               priority="Highest", labels=["ps5-blocker"], created=fri, reporter_email=MEMBER)
    fri_row = build_region_counts(AMER, [t], [], _pulses(), utc(2026, 6, 15))[1]
    assert fri_row.new_highest.count == 1
    assert fri_row.new_pr_mp.count == 0
    assert fri_row.new_ps5.count == 0
    assert fri_row.new_total.count == 1


def test_closed_isreq_buckets_to_done_day():
    rows = _build(utc(2026, 6, 15))
    fri = rows[1]
    assert fri.closed_total.count == 1
    assert fri.closed_highest.count == 1
    assert fri.closed_ps5.count == 0
    assert rows[0].closed_total.count == 0


def test_tooltips_break_down_by_reporter_and_assignee():
    rows = _build(utc(2026, 6, 15))
    fri = rows[1]
    # New tickets attribute to the reporter (roster name + derived name).
    assert fri.new_total.breakdown == {"Alexandre Gomes": 3, "Casey Partner": 1}
    assert "Alexandre Gomes ×3" in fri.new_total.tip
    # Closed tickets attribute to the assignee.
    assert fri.closed_total.breakdown == {"Alexandre Gomes": 1}


def test_weekend_row_combines_saturday_and_sunday_alerts():
    rows = _build(utc(2026, 6, 15))
    weekend = rows[2]
    assert weekend.alerts_ack.count == 1
    assert weekend.alerts_resolved.count == 1
    assert weekend.alerts_total.count == 2
    # Only-region alerts → region is 100% of the global total that weekend.
    assert weekend.region_alert_pct == 100.0


def test_pulse_total_sums_new_closed_and_alerts():
    rows = _build(utc(2026, 6, 15))
    total = rows[-1]
    assert total.new_total.count == 4
    assert total.closed_total.count == 1
    assert total.alerts_total.count == 2
    assert total.region_alert_pct is None


def test_closes_before_pulse_start_are_excluded():
    # A ticket Done before the pulse-12 anchor (Jun 8) — rolled into this sprint
    # by Jira — must NOT be counted as closed this pulse (#93).
    rolled = Ticket(id="ISReq-OLD", project_key="ISReq", title="old", status="Done",
                    priority="Highest", labels=[], is_done_date=date(2026, 6, 6),
                    assignee_email=MEMBER)
    rows = build_region_counts(AMER, [rolled], [], _pulses(), utc(2026, 6, 15))
    assert rows[-1].closed_total.count == 0


def test_days_capped_at_today():
    # Today is the Thursday the sprint starts → a single row (within pulse 12).
    rows = _build(utc(2026, 6, 11))
    day_rows = [r for r in rows if not r.is_total]
    assert len(day_rows) == 1
    assert day_rows[0].label.startswith("Thu")
