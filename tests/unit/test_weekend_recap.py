"""Previous-weekend on-call recap (#145)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from standup_dashboard.domain.models import Alert, AlertState, WeekendOnCall
from standup_dashboard.web.presenters import DashboardData, build_weekend_recap

MEMBER = "alexandre.gomes@canonical.com"  # AMER roster member
OTHER = "someone.else@external.com"


def _at(d, h):
    return datetime(2026, 6, d, h, tzinfo=UTC)


def test_recap_summarizes_oncall_incidents():
    oc = WeekendOnCall(engineer_email=MEMBER, weekend_start=date(2026, 6, 13),
                       weekend_end=date(2026, 6, 14))
    alerts = [
        # INC1: ack 15:00 → resolve 16:00 Sat (1h).
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, _at(13, 15), title="disk full",
              number=42, url="http://pd/42"),
        Alert("INC1", MEMBER, AlertState.RESOLVED, _at(13, 16), title="disk full",
              number=42, url="http://pd/42"),
        # INC2: only acked Sun → still open.
        Alert("INC2", MEMBER, AlertState.ACKNOWLEDGED, _at(14, 15), title="cpu high", number=43),
        # Handled by someone else → excluded.
        Alert("INC3", OTHER, AlertState.RESOLVED, _at(13, 17), title="noise", number=44),
        # On-call but outside the weekend window → excluded.
        Alert("INC4", MEMBER, AlertState.RESOLVED, _at(10, 15), title="weekday", number=45),
    ]
    data = DashboardData(fetched_at=_at(15, 9), alerts=alerts, weekend_oncall=[oc])
    recap = build_weekend_recap(data)

    assert recap is not None
    assert recap.oncall_name == "Alexandre Gomes"
    assert recap.incident_count == 2          # INC1 + INC2 only
    assert recap.resolved == 1
    assert recap.open_acks == 1
    assert recap.incidents[0]["number"] == 43  # open one sorts first
    assert recap.incidents[0]["resolved"] is False


def test_recap_resolve_time_uses_ack_to_resolve():
    oc = WeekendOnCall(engineer_email=MEMBER, weekend_start=date(2026, 6, 13),
                       weekend_end=date(2026, 6, 14))
    alerts = [
        Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, _at(13, 15), title="x", number=1),
        Alert("INC1", MEMBER, AlertState.RESOLVED, _at(13, 17), title="x", number=1),  # 2h
    ]
    data = DashboardData(fetched_at=_at(15, 9), alerts=alerts, weekend_oncall=[oc])
    recap = build_weekend_recap(data)
    assert recap.mttr_label == "2h"
    assert recap.incidents[0]["duration_label"] == "2h"


def test_recap_none_without_oncall():
    assert build_weekend_recap(DashboardData(fetched_at=_at(15, 9))) is None
