"""Growing per-pulse history with per-person tooltips + attribution (#80)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, Color, Pulse, Ticket
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
        # Closed-not-new: created before the pulse, in the AMER window → AMER.
        Ticket("ISReq-C", "ISReq", "z", "Done", "Highest",
               assignee_email=MEMBER, created=utc(2), is_done_date=date(2026, 6, 12)),
        Ticket("ISDB-C", "ISDB", "d", "Done", None,
               assignee_email=MEMBER, created=utc(2), is_done_date=date(2026, 6, 12)),
    ]
    alerts = [Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12))]
    return DashboardData(fetched_at=utc(12), tickets=tickets, alerts=alerts, pulses=pulses)


def test_history_attribution_requestor_vs_assignee(tmp_path):
    db = Database(tmp_path / "t.db")
    data = _data()
    rows = build_pulse_history(db, data, ["AMER"], utc(12, 19))
    cur = {r.pulse_number: r for r in rows}[12].cells
    # New tickets — including [PR/MP Review] — break down by requestor (reporter).
    assert cur["new_highest"].count == 1
    assert cur["new_highest"].breakdown == {"Jane Doe": 1}
    assert cur["new_pr_mp"].breakdown == {"Jane Doe": 1}
    assert cur["new_total"].breakdown == {"Jane Doe": 2}        # both new, by requestor
    # Closed breaks down by assignee.
    assert cur["closed_total"].breakdown == {"Alexandre Gomes": 1}
    # Only-AMER alert/closed → AMER is 100% of all alerts and closes that pulse.
    cur_row = {r.pulse_number: r for r in rows}[12]
    assert cur_row.region_pct == 100.0
    assert cur_row.closed_pct == 100.0
    assert cur["isdb_closed"].count == 1          # the closed ISDB ticket
    assert cur_row.isdb_closed_pct == 100.0
    db.close()


def test_history_persists_counts_and_breakdowns(tmp_path):
    db = Database(tmp_path / "t.db")
    data = _data()
    counts.persist_pulse_summaries(db, data.tickets, [], data.pulses, utc(12))
    stored = {(p, r): (c, b) for p, r, c, b in db.get_pulse_summaries()}
    c12, b12 = stored[(12, "AMER")]
    assert c12["new_total"] == 2 and c12["closed_total"] == 1 and c12["isdb_closed"] == 1
    assert b12["new_highest"] == {"Jane Doe": 1}        # requestor persisted
    assert b12["closed_total"] == {"Alexandre Gomes": 1}
    assert (11, "AMER") in stored                        # previous pulse stored too
    db.close()


def test_history_mttr_from_ack_to_resolve(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    # INC1 acked 10:00 → resolved 12:00 UTC (2h); INC2 only acked (no resolve).
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 10)),
        Alert("INC1", MEMBER, AlertState.RESOLVED, utc(12, 12)),
        Alert("INC2", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 10)),
    ]
    data = DashboardData(fetched_at=utc(12), tickets=[], alerts=alerts, pulses=pulses)
    row = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}[12]
    # Only the ack+resolve incident counts toward MTTR; the ack-only one is ignored.
    assert row.alert_mttr_seconds == 2 * 3600
    assert row.mttr_label == "2h"
    db.close()


def test_history_alert_levels_wired(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    # INC1 triggered 12:00 → acked 12:03 (MTTA 3m) → resolved 12:33 (MTTR 30m).
    alerts = [
        Alert("INC1", "", AlertState.TRIGGERED, utc(12, 12)),
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 12, 3, tzinfo=UTC)),
        Alert("INC1", MEMBER, AlertState.RESOLVED, datetime(2026, 6, 12, 12, 33, tzinfo=UTC)),
    ]
    data = DashboardData(fetched_at=utc(12), tickets=[], alerts=alerts, pulses=pulses)
    row = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}[12]
    assert row.ack_level is Color.GREEN        # 1 alert ≪ pulse cap (56)
    assert row.total_level is Color.GREEN
    assert row.resolved_level is Color.GREEN   # 1 resolved / 1 acked = 100%
    assert row.mttr_level is Color.GREEN       # 30m
    assert row.mtta_level is Color.GREEN       # 3m
    db.close()


def test_history_ack_level_scales_with_selected_region_count(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    # 57 acked incidents > single-region pulse cap (56) → yellow for one region.
    alerts = [Alert(f"INC{i}", MEMBER, AlertState.ACKNOWLEDGED, utc(12)) for i in range(57)]
    data = DashboardData(fetched_at=utc(12), tickets=[], alerts=alerts, pulses=pulses)
    one = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}[12]
    assert one.cells["alerts_ack"].count == 57
    assert one.ack_level is Color.YELLOW
    # Selecting a second region doubles the cap to 112 → 57 is healthy again.
    two = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER", "APAC"], utc(12, 19))}[12]
    assert two.ack_level is Color.GREEN
    db.close()


def test_history_cycle_time_created_to_done(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    # ISReq-A: created Jun 8 → done Jun 12 = 4 days; ISReq-B: Jun 10 → Jun 12 = 2 days.
    tickets = [
        Ticket("ISReq-A", "ISReq", "x", "Done", "Highest", assignee_email=MEMBER,
               created=utc(8), is_done_date=date(2026, 6, 12)),
        Ticket("ISReq-B", "ISReq", "y", "Done", "Medium", assignee_email=MEMBER,
               created=utc(10), is_done_date=date(2026, 6, 12)),
    ]
    data = DashboardData(fetched_at=utc(12), tickets=tickets, alerts=[], pulses=pulses)
    row = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}[12]
    assert row.ticket_cycle_days == 3.0    # mean of 4 and 2 days
    assert row.cycle_label == "3.0d"
    db.close()


def test_mttr_mtta_delta_label():
    from standup_dashboard.domain.models import PulseHistoryRow
    f = PulseHistoryRow._delta_label
    assert f(None) == ""        # no baseline
    assert f(0) == ""           # no change
    assert f(20) == ""          # <1m rounds away
    assert f(120) == "▲2m"      # slower by 2m vs previous pulse
    assert f(-180) == "▼3m"     # faster by 3m
    assert f(3600) == "▲1h"     # larger gap


def test_history_mttr_mtta_delta_vs_previous_pulse(tmp_path):
    db = Database(tmp_path / "t.db")
    # Two stored historical pulses: MTTA 4m→7m (▲3m), MTTR 1h→1h20m (▲20m).
    db.upsert_pulse_summary(10, "AMER", {
        "alert_mtta_sum": 240, "alert_mtta_n": 1,
        "alert_mttr_sum": 3600, "alert_mttr_n": 1}, {}, utc(12))
    db.upsert_pulse_summary(11, "AMER", {
        "alert_mtta_sum": 420, "alert_mtta_n": 1,
        "alert_mttr_sum": 4800, "alert_mttr_n": 1}, {}, utc(12))
    data = DashboardData(fetched_at=utc(12), tickets=[], alerts=[],
                         pulses=[Pulse("ISReq", 201, "s", utc(8), utc(20))])
    rows = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}
    assert rows[10].mtta_delta_label == "" and rows[10].mttr_delta_label == ""  # no baseline
    assert rows[11].mtta_delta_label == "▲3m"   # 7m vs 4m
    assert rows[11].mttr_delta_label == "▲20m"  # 1h20m vs 1h
    db.close()


def test_business_days_excludes_weekends():
    from standup_dashboard.services.counts import _business_days
    assert _business_days(date(2026, 6, 5), date(2026, 6, 8)) == 1   # Fri → Mon
    assert _business_days(date(2026, 6, 8), date(2026, 6, 8)) == 0   # same day
    assert _business_days(date(2026, 6, 8), date(2026, 6, 12)) == 4  # Mon → Fri
    assert _business_days(date(2026, 6, 8), date(2026, 6, 9)) == 1   # Mon → Tue
    assert _business_days(date(2026, 6, 8), date(2026, 6, 22)) == 10 # +2 full weeks
    assert _business_days(date(2026, 6, 12), date(2026, 6, 5)) == 0  # done before created


def test_accumulated_pulse_alerts_unions_fetches(tmp_path):
    # PagerDuty is fetched incrementally, so an incident's ack and resolve can land
    # in different fetch snapshots. The persisted summary must see both (#140).
    db = Database(tmp_path / "t.db")
    f1 = db.create_fetch_snapshot(fetched_at=utc(11), jira_ok=True,
                                  pagerduty_ok=True, ical_ok=True, raw_path="")
    db.insert_alerts(f1, [Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(11, 10))])
    f2 = db.create_fetch_snapshot(fetched_at=utc(13), jira_ok=True,
                                  pagerduty_ok=True, ical_ok=True, raw_path="")
    db.insert_alerts(f2, [Alert("INC1", MEMBER, AlertState.RESOLVED, utc(11, 12))])
    alerts = counts.accumulated_pulse_alerts(db, utc(13))
    assert len(alerts) == 2
    assert {a.state for a in alerts} == {AlertState.ACKNOWLEDGED, AlertState.RESOLVED}
    db.close()


def test_history_mttr_persists_and_reads_back(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 10)),
        Alert("INC1", MEMBER, AlertState.RESOLVED, utc(12, 13)),   # 3h
    ]
    counts.persist_pulse_summaries(db, [], alerts, pulses, utc(12))
    c12 = {(p, r): c for p, r, c, _ in db.get_pulse_summaries()}[(12, "AMER")]
    assert c12["alert_mttr_sum"] == 3 * 3600 and c12["alert_mttr_n"] == 1
    db.close()


def test_history_mtta_from_trigger_to_ack(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    # INC1 triggered 10:00 → acked 12:00 UTC (2h). INC2 acked with no trigger → ignored.
    alerts = [
        Alert("INC1", "", AlertState.TRIGGERED, utc(12, 10)),
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 12)),
        Alert("INC2", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 10)),
    ]
    data = DashboardData(fetched_at=utc(12), tickets=[], alerts=alerts, pulses=pulses)
    row = {r.pulse_number: r for r in build_pulse_history(db, data, ["AMER"], utc(12, 19))}[12]
    assert row.alert_mtta_seconds == 2 * 3600
    assert row.mtta_label == "2h"
    db.close()


def test_history_mtta_persists_and_reads_back(tmp_path):
    db = Database(tmp_path / "t.db")
    pulses = [Pulse("ISReq", 201, "s", utc(8), utc(20))]
    alerts = [
        Alert("INC1", "", AlertState.TRIGGERED, utc(12, 10)),
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, utc(12, 13)),   # 3h
    ]
    counts.persist_pulse_summaries(db, [], alerts, pulses, utc(12))
    c12 = {(p, r): c for p, r, c, _ in db.get_pulse_summaries()}[(12, "AMER")]
    assert c12["alert_mtta_sum"] == 3 * 3600 and c12["alert_mtta_n"] == 1
    db.close()
