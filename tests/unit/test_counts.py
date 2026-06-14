"""US3 + #91 unit tests: ISReq new/closed buckets, per-person tooltips, weekend."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, Color, Pulse, Ticket
from standup_dashboard.services.counts import build_counts, build_region_counts

AMER = "AMER"
MEMBER = "alexandre.gomes@canonical.com"   # AMER → "Alexandre Gomes"
OTHER = "colin.misare@canonical.com"       # AMER → "Colin Misare"


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
        # New ISReq tickets created Friday — one per bucket (counted by assignee).
        Ticket(id="ISReq-H", project_key="ISReq", title="boom", status="To Do",
               priority="Highest", labels=[], created=fri, assignee_email=MEMBER),
        Ticket(id="ISReq-PR", project_key="ISReq", title="[PR/MP Review] x", status="To Do",
               priority="Medium", labels=[], created=fri, assignee_email=MEMBER),
        Ticket(id="ISReq-P5", project_key="ISReq", title="blk", status="To Do",
               priority="Medium", labels=["ps5-blocker"], created=fri, assignee_email=OTHER),
        Ticket(id="ISReq-R", project_key="ISReq", title="reg", status="To Do",
               priority="Medium", labels=[], created=fri, assignee_email=MEMBER),
        # Closed ISReq Highest on Friday. Created before the pulse, in the AMER
        # window (15–23 UTC) → owned by AMER, closed-not-new this pulse.
        Ticket(id="ISReq-C", project_key="ISReq", title="done", status="Done",
               priority="Highest", labels=[], created=utc(2026, 6, 5),
               is_done_date=date(2026, 6, 12), assignee_email=MEMBER),
        # Non-ISReq (ISDB) new ticket is ignored by the ISReq columns.
        Ticket(id="ISDB-1", project_key="ISDB", title="x", status="To Do",
               priority="Highest", labels=[], created=fri, assignee_email=MEMBER),
        # A closed ISDB ticket → counted in the ISDB Closed column.
        Ticket(id="ISDB-C", project_key="ISDB", title="d", status="Done",
               priority=None, labels=[], created=utc(2026, 6, 5),
               is_done_date=date(2026, 6, 12), assignee_email=MEMBER),
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
    assert rows[0].label.startswith("Thu")
    assert rows[1].label.startswith("Fri")
    assert rows[2].is_weekend and "Sat–Sun" in rows[2].label
    assert rows[3].label.startswith("Mon")
    assert any(r.label == "Pulse total" for r in rows)
    assert any(r.is_previous for r in rows)  # previous-pulse comparison row (#80)


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
               priority="Highest", labels=["ps5-blocker"], created=fri, assignee_email=MEMBER)
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
    assert fri.closed_pct == 100.0   # AMER closed the only ISReq closed ticket that day
    assert fri.isdb_closed.count == 1        # the ISDB closed ticket counts separately
    assert fri.isdb_closed_pct == 100.0
    assert rows[0].closed_total.count == 0


def test_tooltips_break_down_by_reporter_and_assignee():
    rows = _build(utc(2026, 6, 15))
    fri = rows[1]
    # New tickets attribute to the assignee (region members only).
    assert fri.new_total.breakdown == {"Alexandre Gomes": 3, "Colin Misare": 1}
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


def _total(rows):
    return next(r for r in rows if r.label == "Pulse total")


def test_pulse_total_sums_new_closed_and_alerts():
    rows = _build(utc(2026, 6, 15))
    total = _total(rows)
    assert total.new_total.count == 4
    assert total.closed_total.count == 1
    assert total.alerts_total.count == 2
    assert total.region_alert_pct is None


def test_closes_before_pulse_start_go_to_previous_pulse_row():
    # A ticket Done before the pulse-12 anchor (Jun 8) is excluded from this
    # pulse (#93) and instead counts in the Previous pulse row (#80).
    rolled = Ticket(id="ISReq-OLD", project_key="ISReq", title="old", status="Done",
                    priority="Highest", labels=[], created=utc(2026, 6, 5),
                    is_done_date=date(2026, 6, 6), assignee_email=MEMBER)
    rows = build_region_counts(AMER, [rolled], [], _pulses(), utc(2026, 6, 15))
    assert _total(rows).closed_total.count == 0
    prev = next(r for r in rows if r.is_previous)
    assert prev.closed_total.count == 1


def test_days_capped_at_today():
    # Today is the Thursday the sprint starts → a single row (within pulse 12).
    rows = _build(utc(2026, 6, 11))
    day_rows = [r for r in rows if not r.is_total]
    assert len(day_rows) == 1
    assert day_rows[0].label.startswith("Thu")


def test_closed_pr_mp_credited_to_assignee_region():
    # A [PR/MP Review] ticket created in the EMEA window but OWNED by an AMER
    # engineer: its PR/MP-closed credit goes to AMER (owner), while the generic
    # closed_total still follows EMEA (creation region).
    t = Ticket(id="ISReq-PRC", project_key="ISReq", title="[PR/MP Review] done",
               status="Done", priority="Medium", labels=[], created=utc(2026, 6, 5, 10),
               is_done_date=date(2026, 6, 12), assignee_email=MEMBER)
    amer_fri = build_region_counts("AMER", [t], [], _pulses(), utc(2026, 6, 15))[1]
    emea_fri = build_region_counts("EMEA", [t], [], _pulses(), utc(2026, 6, 15))[1]
    assert amer_fri.closed_pr_mp.count == 1
    assert amer_fri.closed_pr_mp.breakdown == {"Alexandre Gomes": 1}
    assert amer_fri.closed_total.count == 0      # creation region is EMEA, not AMER
    assert emea_fri.closed_pr_mp.count == 0      # owner is AMER, not EMEA
    assert emea_fri.closed_total.count == 1      # but the generic close is EMEA's


def test_daily_mttr_and_mtta_per_row_and_total():
    # One incident on Friday: trigger 18:00, first ack 18:10 (MTTA 10m), resolve
    # 18:40 (MTTR 30m). All three events land on the same region-local day
    # (18:00 UTC ≈ 12:00 Mexico City), so the Friday row and the pulse total agree.
    alerts = [
        Alert("INC", "", AlertState.TRIGGERED, utc(2026, 6, 12, 18)),
        Alert("INC", MEMBER, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, 10, tzinfo=UTC)),
        Alert("INC", MEMBER, AlertState.RESOLVED, datetime(2026, 6, 12, 18, 40, tzinfo=UTC)),
    ]
    rows = build_region_counts(AMER, [], alerts, _pulses(), utc(2026, 6, 15))
    fri = next(r for r in rows if not r.is_total and r.alert_mttr_n)
    assert fri.alert_mttr_n == 1 and fri.mttr_label == "30m"
    assert fri.alert_mtta_n == 1 and fri.mtta_label == "10m"
    total = _total(rows)
    assert total.alert_mttr_seconds == 1800 and total.mttr_label == "30m"
    assert total.alert_mtta_seconds == 600 and total.mtta_label == "10m"


def test_daily_mttr_blank_when_no_resolve_pair():
    # An ack with no matching resolve yields no MTTR pairing → None / em dash.
    alerts = [Alert("INC", MEMBER, AlertState.ACKNOWLEDGED, utc(2026, 6, 12, 18))]
    total = _total(build_region_counts(AMER, [], alerts, _pulses(), utc(2026, 6, 15)))
    assert total.alert_mttr_n == 0 and total.alert_mttr_seconds is None
    assert total.mttr_label == "—"


def test_daily_alert_levels_wired():
    # trigger 18:00 · ack 18:10 (MTTA 10m → yellow) · resolve 18:40 (MTTR 30m → green).
    alerts = [
        Alert("INC", "", AlertState.TRIGGERED, utc(2026, 6, 12, 18)),
        Alert("INC", MEMBER, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, 10, tzinfo=UTC)),
        Alert("INC", MEMBER, AlertState.RESOLVED, datetime(2026, 6, 12, 18, 40, tzinfo=UTC)),
    ]
    total = _total(build_region_counts(AMER, [], alerts, _pulses(), utc(2026, 6, 15)))
    assert total.mttr_level is Color.GREEN       # 30m ≤ 30m
    assert total.mtta_level is Color.YELLOW      # 10m in (5m, 15m]
    assert total.resolved_level is Color.GREEN   # 1 resolved / 1 acked = 100%


def test_ack_total_level_scales_with_selected_region_count():
    # Three weekday acks by AMER members → over the single-region weekday cap (2).
    fri = utc(2026, 6, 12)
    alerts = [Alert(f"INC{i}", MEMBER, AlertState.ACKNOWLEDGED, fri) for i in range(3)]
    amer_fri = build_region_counts(AMER, [], alerts, _pulses(), utc(2026, 6, 15))[1]
    assert amer_fri.alerts_ack.count == 3
    assert amer_fri.ack_level is Color.YELLOW     # 3 > cap 2 (one region)
    assert amer_fri.total_level is Color.YELLOW
    # Selecting a second region doubles the cap to 4 → 3 is healthy again.
    both_fri = build_counts([AMER, "APAC"], [], alerts, _pulses(), utc(2026, 6, 15))[1]
    assert both_fri.ack_level is Color.GREEN
    assert both_fri.total_level is Color.GREEN


def test_closed_pr_mp_keep_up_colour():
    # 2 reviews requested Friday, 1 closed → 1 behind → yellow ("ok to leave one").
    fri = utc(2026, 6, 12)
    tickets = [
        Ticket(id="ISReq-PR1", project_key="ISReq", title="[PR/MP Review] a", status="To Do",
               priority="Medium", labels=[], created=fri, assignee_email=MEMBER,
               reporter_email=OTHER),
        Ticket(id="ISReq-PR2", project_key="ISReq", title="[PR/MP Review] b", status="To Do",
               priority="Medium", labels=[], created=fri, assignee_email=MEMBER,
               reporter_email=OTHER),
        Ticket(id="ISReq-PRC", project_key="ISReq", title="[PR/MP Review] c", status="Done",
               priority="Medium", labels=[], created=utc(2026, 6, 5),
               is_done_date=date(2026, 6, 12), assignee_email=MEMBER),
    ]
    fri_row = build_region_counts(AMER, tickets, [], _pulses(), utc(2026, 6, 15))[1]
    assert fri_row.new_pr_mp.count == 2 and fri_row.closed_pr_mp.count == 1
    assert fri_row.closed_pr_mp_level is Color.YELLOW
    # Close the second one too → kept up → green.
    tickets.append(Ticket(id="ISReq-PRC2", project_key="ISReq", title="[PR/MP Review] d",
                          status="Done", priority="Medium", labels=[], created=utc(2026, 6, 5),
                          is_done_date=date(2026, 6, 12), assignee_email=MEMBER))
    fri_row = build_region_counts(AMER, tickets, [], _pulses(), utc(2026, 6, 15))[1]
    assert fri_row.closed_pr_mp.count == 2 and fri_row.closed_pr_mp_level is Color.GREEN


def test_region_follows_creation_time_not_assignee():
    # Ticket created at 10:00 UTC (EMEA window) but assigned to an AMER engineer:
    # it belongs to EMEA (creation time), not AMER (assignee).
    fri_emea = utc(2026, 6, 12, 10)   # 10:00 UTC → EMEA window (07–15)
    t_new = Ticket(id="ISReq-E", project_key="ISReq", title="x", status="To Do",
                   priority="Highest", labels=[], created=fri_emea, assignee_email=MEMBER)
    t_closed = Ticket(id="ISReq-EC", project_key="ISReq", title="y", status="Done",
                      priority="Highest", labels=[], created=utc(2026, 6, 5, 10),
                      is_done_date=date(2026, 6, 12), assignee_email=MEMBER)
    tickets = [t_new, t_closed]
    amer = build_region_counts("AMER", tickets, [], _pulses(), utc(2026, 6, 15))
    emea = build_region_counts("EMEA", tickets, [], _pulses(), utc(2026, 6, 15))
    # AMER sees neither, despite the AMER assignee.
    assert next(r for r in amer if r.is_total and not r.is_previous).new_total.count == 0
    # EMEA owns both the new and the closed ticket (bucketed in Paris-local days).
    emea_fri = emea[1]
    assert emea_fri.new_highest.count == 1
    assert emea_fri.closed_total.count == 1
