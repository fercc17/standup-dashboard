"""Previous-weekend on-call recap (#145) — reads weekend alerts from the DB."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, WeekendOnCall
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import DashboardData, build_weekend_recap

MEMBER = "alexandre.gomes@canonical.com"  # AMER (America/Mexico_City, UTC-6)
OTHER = "someone.else@external.com"
OC = WeekendOnCall(engineer_email=MEMBER, weekend_start=date(2026, 6, 13),
                   weekend_end=date(2026, 6, 14))


def _at(d, h, m=0):
    return datetime(2026, 6, d, h, m, tzinfo=UTC)


def _db(db_dsn, alerts):
    db = Database(db_dsn)
    fid = db.create_fetch_snapshot(fetched_at=_at(15, 9), jira_ok=True,
                                   pagerduty_ok=True, ical_ok=True, raw_path="")
    db.insert_alerts(fid, alerts)
    return db


def test_next_vs_current_oncall_split(tmp_path, db_dsn):
    # Two stored weekends: the header shows the upcoming one, the recap the passed.
    cur = WeekendOnCall(engineer_email=MEMBER, weekend_start=date(2026, 6, 13),
                        weekend_end=date(2026, 6, 14))
    nxt = WeekendOnCall(engineer_email="haw.loeung@canonical.com",
                        weekend_start=date(2026, 6, 20), weekend_end=date(2026, 6, 21))
    data = DashboardData(fetched_at=_at(15, 9), weekend_oncall=[nxt, cur])  # order-independent
    assert data.oncall_email == MEMBER                            # earliest = current/passed
    assert data.next_oncall_email == "haw.loeung@canonical.com"   # latest = upcoming (header)
    recap = build_weekend_recap(_db(db_dsn, []), data, _at(15, 9))
    assert recap.oncall_name == "Alexandre Gomes"                 # recap names the passed weekend


def test_recap_summarizes_oncall_incidents(tmp_path, db_dsn):
    alerts = [
        # INC1: trigger 14:30 → ack 15:00 (MTTA 30m) → resolve 16:00 Sat (MTTR 1h).
        Alert("INC1", "", AlertState.TRIGGERED, _at(13, 14, 30), title="disk full",
              number=42, url="http://pd/42"),
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, _at(13, 15), title="disk full",
              number=42, url="http://pd/42"),
        Alert("INC1", MEMBER, AlertState.RESOLVED, _at(13, 16), title="disk full",
              number=42, url="http://pd/42"),
        # INC2: trigger 14:40 → ack 15:00 Sun (MTTA 20m), still open (no MTTR).
        Alert("INC2", "", AlertState.TRIGGERED, _at(14, 14, 40), title="cpu high", number=43),
        Alert("INC2", MEMBER, AlertState.ACKNOWLEDGED, _at(14, 15), title="cpu high", number=43),
        # Handled by someone else → excluded.
        Alert("INC3", OTHER, AlertState.RESOLVED, _at(13, 17), title="noise", number=44),
        # On-call but outside the weekend window → excluded.
        Alert("INC4", MEMBER, AlertState.RESOLVED, _at(10, 15), title="weekday", number=45),
    ]
    db = _db(db_dsn, alerts)
    recap = build_weekend_recap(
        db, DashboardData(fetched_at=_at(15, 9), weekend_oncall=[OC]), _at(15, 9))

    assert recap is not None
    assert recap.oncall_name == "Alexandre Gomes"
    assert recap.incident_count == 2          # INC1 + INC2 only
    assert recap.resolved == 1
    assert recap.open_acks == 1
    assert recap.incidents[0]["number"] == 43  # open one sorts first
    assert recap.incidents[0]["resolved"] is False
    assert recap.mttr_label == "1h"            # only INC1 resolved (ack→resolve = 1h)
    assert recap.mtta_label == "25m"           # mean of 30m (INC1) + 20m (INC2)
    db.close()


def test_recap_counts_resolution_that_slips_past_midnight(tmp_path, db_dsn):
    # Acked Sun 23:30 local (Mon 05:30 UTC), resolved Mon 00:30 local (Mon 06:30 UTC).
    # The resolution slips just past the weekend, but must still read as resolved (#145).
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, _at(15, 5, 30), title="x", number=1),
        Alert("INC1", MEMBER, AlertState.RESOLVED, _at(15, 6, 30), title="x", number=1),
    ]
    db = _db(db_dsn, alerts)
    recap = build_weekend_recap(
        db, DashboardData(fetched_at=_at(15, 9), weekend_oncall=[OC]), _at(15, 9))
    assert recap.incident_count == 1
    assert recap.resolved == 1 and recap.open_acks == 0
    assert recap.mttr_label == "1h"
    assert recap.mtta_label == "—"   # no TRIGGERED event loaded → MTTA unknown
    db.close()


def test_recap_splits_in_hours_vs_off_hours_and_sums_time(tmp_path, db_dsn):
    # AMER = UTC-6; business hours 09:00–17:00 local = 15:00–23:00 UTC.
    alerts = [
        # INC1 in-hours: fired 18:00 UTC = 12:00 local Sat; ack 18:10 → resolve 18:40 (30m).
        Alert("INC1", "", AlertState.TRIGGERED, _at(13, 18), number=1),
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, _at(13, 18, 10), number=1),
        Alert("INC1", MEMBER, AlertState.RESOLVED, _at(13, 18, 40), number=1),
        # INC2 off-hours: fired 06:00 UTC = 00:00 local Sun; ack 06:05 → resolve 06:35 (30m).
        Alert("INC2", "", AlertState.TRIGGERED, _at(14, 6), number=2),
        Alert("INC2", MEMBER, AlertState.ACKNOWLEDGED, _at(14, 6, 5), number=2),
        Alert("INC2", MEMBER, AlertState.RESOLVED, _at(14, 6, 35), number=2),
    ]
    db = _db(db_dsn, alerts)
    recap = build_weekend_recap(
        db, DashboardData(fetched_at=_at(15, 9), weekend_oncall=[OC]), _at(15, 9))
    assert recap.total_time_label == "1h"                       # 30m + 30m invested
    assert (recap.in_hours_count, recap.off_hours_count) == (1, 1)
    assert recap.in_hours_time_label == "30m"
    assert recap.off_hours_time_label == "30m"
    # The off-hours incident is flagged per-line too.
    by_num = {i["number"]: i for i in recap.incidents}
    assert by_num[2]["off_hours"] is True
    assert by_num[1]["off_hours"] is False
    db.close()


def test_recap_none_without_oncall(tmp_path, db_dsn):
    db = _db(db_dsn, [])
    assert build_weekend_recap(db, DashboardData(fetched_at=_at(15, 9)), _at(15, 9)) is None
    db.close()
