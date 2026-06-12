"""Weekend on-call resolution (FR-025) — T049.

Parses the iCal feed to find the single engineer covering the weekend, matches
them to the roster (by ATTENDEE email or SUMMARY name), and exposes helpers for
"all others OFF on Sat/Sun" and the Monday combined-weekend window. Pure over
the feed text + a reference date so it is deterministically unit-testable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from icalendar import Calendar

from .. import config
from ..domain.models import Role, WeekendOnCall


def weekend_for(reference: date) -> tuple[date, date]:
    """Return (Saturday, Sunday) of the weekend associated with ``reference``.

    For a Monday this is the immediately preceding weekend; for a weekday it is
    the most recent past weekend; for Sat/Sun it is that weekend.
    """
    days_since_sat = (reference.weekday() - 5) % 7
    sat = reference - timedelta(days=days_since_sat)
    return sat, sat + timedelta(days=1)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _attendee_emails(event) -> list[str]:
    raw = event.get("ATTENDEE")
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for v in values:
        s = str(v)
        out.append(s.split(":", 1)[1] if s.lower().startswith("mailto:") else s)
    return out


def _match_roster(event) -> str | None:
    for email in _attendee_emails(event):
        if email in config.ENGINEERS_BY_EMAIL:
            return email
    summary = str(event.get("SUMMARY", "")).lower()
    for eng in config.ROSTER:
        if eng.email.lower() in summary or eng.name.lower() in summary:
            return eng.email
    return None


def resolve_oncall(ical_text: str, reference: date) -> WeekendOnCall | None:
    """Find the weekend on-call engineer for the weekend of ``reference``."""
    sat, sun = weekend_for(reference)
    cal = Calendar.from_ical(ical_text)
    for event in cal.walk("VEVENT"):
        start = _as_date(event.get("DTSTART").dt) if event.get("DTSTART") else None
        end = _as_date(event.get("DTEND").dt) if event.get("DTEND") else None
        if start is None:
            continue
        # iCal DTEND is exclusive; treat the covered span as [start, end).
        last = (end - timedelta(days=1)) if end else start
        if start <= sun and last >= sat:  # overlaps the weekend
            email = _match_roster(event)
            if email:
                return WeekendOnCall(engineer_email=email, weekend_start=sat, weekend_end=sun)
    return None


def others_off(oncall_email: str | None, member_emails: list[str]) -> dict[str, Role]:
    """Every member except the on-call engineer is OFF on the weekend (FR-025)."""
    return {e: Role.OFF for e in member_emails if e != oncall_email}
