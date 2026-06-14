"""Repeat-offender alert grouping (#146), region-scoped."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import Alert, AlertState
from standup_dashboard.web.presenters import DashboardData, build_repeat_offenders

A = "alexandre.gomes@canonical.com"  # AMER member
B = "other.person@external.com"      # out-of-scope handler


def _at(h):
    return datetime(2026, 6, 13, h, tzinfo=UTC)


def test_groups_by_normalized_title_over_min_count():
    alerts = [
        # INC1 (2 events) + INC2 share a title (after whitespace/case normalise) → 2 distinct.
        Alert("INC1", A, AlertState.ACKNOWLEDGED, _at(9), title="Disk full on db1", number=1),
        Alert("INC1", A, AlertState.RESOLVED, _at(10), title="Disk full on db1", number=1),
        Alert("INC2", B, AlertState.RESOLVED, _at(11), title="disk full  on db1 ", number=7,
              url="http://pd/7"),
        # Fired only once → not a repeat offender.
        Alert("INC3", A, AlertState.ACKNOWLEDGED, _at(12), title="CPU high", number=9),
        # No title → ignored.
        Alert("INC4", A, AlertState.ACKNOWLEDGED, _at(13), title=None, number=10),
    ]
    rows = build_repeat_offenders(DashboardData(fetched_at=_at(14), alerts=alerts), {A, B})

    assert len(rows) == 1
    row = rows[0]
    assert row.count == 2                       # distinct incidents INC1 + INC2
    assert row.title == "Disk full on db1"      # display keeps the first-seen original
    assert row.number == 7 and row.url == "http://pd/7"   # latest event is representative
    assert row.handlers == ["Alexandre Gomes", B]         # both handlers, sorted


def test_region_scoped_and_skips_handlerless():
    alerts = [
        Alert("INC1", A, AlertState.ACKNOWLEDGED, _at(9), title="dup", number=1),
        Alert("INC2", A, AlertState.RESOLVED, _at(10), title="dup", number=2),
        Alert("INC3", B, AlertState.ACKNOWLEDGED, _at(11), title="dup", number=3),  # out of region
        Alert("INC4", "", AlertState.TRIGGERED, _at(12), title="dup", number=4),    # handler-less
    ]
    rows = build_repeat_offenders(DashboardData(fetched_at=_at(13), alerts=alerts), {A})
    assert len(rows) == 1
    assert rows[0].count == 2                    # only A's INC1 + INC2
    assert rows[0].handlers == ["Alexandre Gomes"]


def test_empty_when_nothing_repeats():
    alerts = [Alert("INC1", A, AlertState.RESOLVED, _at(9), title="one-off", number=1)]
    assert build_repeat_offenders(DashboardData(fetched_at=_at(10), alerts=alerts), {A}) == []
