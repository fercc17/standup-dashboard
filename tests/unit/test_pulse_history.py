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
