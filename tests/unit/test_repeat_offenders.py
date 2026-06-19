"""Repeat-offender analysis (#146): year-history backed, last-10-day + >10/yr."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import Alert, AlertState
from standup_dashboard.services.offenders import (
    IncidentRecord,
    build_offenders,
    incident_signature,
    incidents_from_alerts,
)
from standup_dashboard.storage.db import Database

NOW = datetime(2026, 6, 14, 12, tzinfo=UTC)


def _dt(m, d, h=12):
    return datetime(2026, m, d, h, tzinfo=UTC)


def test_incident_signature_strips_volatile_prefix():
    assert incident_signature("[FIRING:3] Git E2E down") == ("Git E2E down", "git e2e down")
    assert incident_signature("[RESOLVED]  Disk  full ")[1] == "disk full"
    assert incident_signature("Plain title") == ("Plain title", "plain title")
    assert incident_signature(None) == ("", "")


def test_incidents_from_alerts_collapses_events_to_one_record():
    alerts = [
        Alert("INC1", "", AlertState.TRIGGERED, _dt(6, 10, 12),
              title="[FIRING:1] Boom", number=5, url="u"),
        Alert("INC1", "a@x", AlertState.ACKNOWLEDGED, _dt(6, 10, 13), title="[FIRING:1] Boom"),
    ]
    recs = incidents_from_alerts(alerts)
    assert len(recs) == 1
    r = recs[0]
    assert r.id == "INC1" and r.signature == "boom" and r.title == "Boom"
    assert r.fired_at == _dt(6, 10, 12)        # earliest (trigger) event
    assert r.number == 5 and r.url == "u"


def test_build_offenders_applies_year_and_recent_windows(tmp_path, db_dsn):
    db = Database(db_dsn)
    recs = []
    # chronic: 10 incidents Jan–May + 1 in the last 10 days → 11 YTD, qualifies.
    recs += [IncidentRecord(f"chr{i}", "chronic", _dt(1 + i % 5, 1 + i), "Chronic alert")
             for i in range(10)]
    recs.append(IncidentRecord("chr-recent", "chronic", _dt(6, 12), "Chronic alert",
                               number=99, url="http://pd/99"))
    # old-noisy: 11 YTD but none in the last 10 days → not "still firing", excluded.
    recs += [IncidentRecord(f"old{i}", "old-noisy", _dt(2, 1 + i), "Old noisy") for i in range(11)]
    # rare: recent but only 5 YTD (≤ 10) → excluded.
    recs += [IncidentRecord(f"rare{i}", "rare", _dt(6, 10 + i % 3), "Rare") for i in range(5)]
    db.upsert_incidents(recs)

    rows = build_offenders(db, NOW)
    assert [r.title for r in rows] == ["Chronic alert"]
    assert rows[0].year_count == 11 and rows[0].recent_count == 1
    assert rows[0].number == 99 and rows[0].url == "http://pd/99"   # representative = latest
    db.close()


def test_handlers_reflect_the_last_10_days(tmp_path, db_dsn):
    db = Database(db_dsn)
    recs = [IncidentRecord(f"c{i}", "chronic", _dt(1 + i % 5, 1 + i), "Chronic") for i in range(11)]
    recs.append(IncidentRecord("c-recent", "chronic", _dt(6, 12), "Chronic"))
    db.upsert_incidents(recs)
    # A recent alert event names who handled it; older/out-of-window events would not.
    f = db.create_fetch_snapshot(fetched_at=NOW, jira_ok=True, pagerduty_ok=True,
                                 ical_ok=True, raw_path="")
    db.insert_alerts(f, [Alert("c-recent", "alexandre.gomes@canonical.com",
                               AlertState.ACKNOWLEDGED, _dt(6, 12), title="[FIRING:1] Chronic")])
    rows = build_offenders(db, NOW)
    assert rows[0].handlers == ["Alexandre Gomes"]
    db.close()


def test_upsert_incidents_keeps_earliest_fired_at(tmp_path, db_dsn):
    db = Database(db_dsn)
    db.upsert_incidents([IncidentRecord("INC", "sig", _dt(6, 10), "t")])
    db.upsert_incidents([IncidentRecord("INC", "sig", _dt(6, 5), "t")])   # earlier re-fire
    [row] = db.get_incidents_since(_dt(1, 1))
    assert row["fired_at"] == _dt(6, 5).isoformat()
    db.close()
