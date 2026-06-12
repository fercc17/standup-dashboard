"""US5 unit tests: on-call resolution, others-OFF, weekend windows (T047)."""

from __future__ import annotations

from datetime import date

from standup_dashboard.domain.models import Role, WeekendOnCall
from standup_dashboard.services.oncall import others_off, resolve_oncall, weekend_for

FERNANDO = "fernando.carrillo.castro@canonical.com"
JAMES = "james.simpson@canonical.com"

ICAL_BY_SUMMARY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//
BEGIN:VEVENT
UID:1
DTSTART;VALUE=DATE:20260613
DTEND;VALUE=DATE:20260615
SUMMARY:On-call: Fernando Carrillo Castro
END:VEVENT
END:VCALENDAR
"""

ICAL_BY_ATTENDEE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20260613
DTEND;VALUE=DATE:20260615
SUMMARY:Weekend shift
ATTENDEE:mailto:james.simpson@canonical.com
END:VEVENT
END:VCALENDAR
"""

ICAL_NO_MATCH = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:3
DTSTART;VALUE=DATE:20260613
DTEND;VALUE=DATE:20260615
SUMMARY:Nobody we know
END:VEVENT
END:VCALENDAR
"""


def test_weekend_for_monday_returns_preceding_weekend():
    assert weekend_for(date(2026, 6, 15)) == (date(2026, 6, 13), date(2026, 6, 14))


def test_weekend_for_saturday_and_sunday():
    assert weekend_for(date(2026, 6, 13)) == (date(2026, 6, 13), date(2026, 6, 14))
    assert weekend_for(date(2026, 6, 14)) == (date(2026, 6, 13), date(2026, 6, 14))


def test_resolve_oncall_by_summary():
    oc = resolve_oncall(ICAL_BY_SUMMARY, date(2026, 6, 15))
    assert oc == WeekendOnCall(FERNANDO, date(2026, 6, 13), date(2026, 6, 14))


def test_resolve_oncall_by_attendee():
    oc = resolve_oncall(ICAL_BY_ATTENDEE, date(2026, 6, 15))
    assert oc is not None and oc.engineer_email == JAMES


def test_resolve_oncall_no_match_returns_none():
    assert resolve_oncall(ICAL_NO_MATCH, date(2026, 6, 15)) is None


def test_others_off_marks_everyone_but_oncall():
    members = [FERNANDO, "a@x", "b@x"]
    result = others_off(FERNANDO, members)
    assert result == {"a@x": Role.OFF, "b@x": Role.OFF}
    assert FERNANDO not in result
