"""Calendar availability from a free/busy iCal feed (#cal).

The public Google iCal feed exposes only opaque "Busy" blocks (no titles,
attendees, or types), so events are classified **purely by duration**:

  * date-type all-day event        → PTO (an explicit day-off entry)
  * timed block ≥ 8h and < 24h     → PTO (a full working day off)
  * timed block ≥ 24h              → ignored: a recurring all-day "busy" artifact
                                      the feed emits (00:00→00:00), not real PTO
  * ~4h                            → SD time (one per ISO week; its weekday marked)
  * > 1h (≤8h)                     → blocker (a "do not book" hold between shifts)
  * ≤ 1h                           → a real meeting

``busy`` = merged wall-clock of the **meetings** only (≤1h blocks); blockers and
SD are *not* counted as busy. ``open`` = capacity (40h/week) − busy. Blockers and
PTO don't reduce ``open``: a >1h blocker is off-time *between* shifts, not part of
the working capacity, and PTO is tracked separately. Pure over the feed text + a
window so it is deterministically unit-testable (mirrors ``services/oncall.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from icalendar import Calendar

from ..config import BUSINESS_HOURS_LOCAL
from ..domain.models import CalendarAvail

WORKDAY_S = 8 * 3600         # 40h/week ÷ 5 = 8h capacity per weekday
PTO_MIN_S = 8 * 3600         # a full working-day block counts as PTO …
# … but only when it actually covers the engineer's local working hours. An 8–12h
# block that sits *overnight* (evening→morning) is a personal/do-not-disturb hold,
# not a day off, so a timed PTO block must overlap [09:00,17:00) local by at least
# this much. Without it, recurring overnight "Busy" blocks read as a week of PTO.
PTO_WORKDAY_OVERLAP_MIN_S = 6 * 3600
MEETING_MAX_S = 3600         # ≤1h is a real meeting; longer is a blocker/SD hold
SD_MIN_S = int(3.5 * 3600)   # a "4h" SD block, with tolerance
SD_MAX_S = int(4.5 * 3600)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _weekday_count(start: datetime, end: datetime) -> int:
    """Number of Mon–Fri dates in the half-open window [start, end)."""
    d, n = start.date(), 0
    while d < end.date():
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _merge_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
    """Wall-clock seconds covered by the union of intervals (overlaps once)."""
    total = 0
    cur_s = cur_e = None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += int((cur_e - cur_s).total_seconds())
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += int((cur_e - cur_s).total_seconds())
    return total


def compute_availability(
    ical_text: str, window_start: datetime, window_end: datetime, tz: tzinfo = UTC
) -> CalendarAvail:
    """Busy/open/PTO/SD for one window from a free/busy iCal feed (parses the feed).
    ``tz`` is the engineer's region timezone — it decides whether a long block sits
    on their working day (PTO) or overnight (a personal hold)."""
    return _availability(Calendar.from_ical(ical_text), window_start, window_end, tz)


def compute_availability_windows(
    ical_text: str, windows: list[tuple[datetime, datetime]], tz: tzinfo = UTC
) -> list[CalendarAvail]:
    """Availability for several windows, parsing the (large) feed only once.

    Parsing a full free/busy feed dominates the cost (≈3s for a 2 MB feed), so the
    fetch path derives the pulse + today windows from a single parse instead of
    re-parsing per window — and runs this off the event loop (it's CPU-bound, and
    blocking the loop would starve the other engineers' concurrent HTTP fetches).
    ``tz`` is the engineer's region timezone (see ``compute_availability``).
    """
    cal = Calendar.from_ical(ical_text)
    return [_availability(cal, s, e, tz) for s, e in windows]


def _availability(
    cal: Calendar, window_start: datetime, window_end: datetime, tz: tzinfo = UTC
) -> CalendarAvail:
    """Busy/open/PTO/SD for ``[window_start, window_end)`` from a parsed feed."""
    meetings: list[tuple[datetime, datetime]] = []   # ≤1h blocks → the busy number
    pto_weekdays: set = set()
    sd_by_week: dict = {}  # (iso-year, iso-week) → weekday abbrev of its 4h block

    def _mark_pto(d0, d1) -> None:  # [d0, d1) over dates, weekdays only, in-window
        d = d0
        while d < d1:
            if window_start.date() <= d < window_end.date() and d.weekday() < 5:
                pto_weekdays.add(d)
            d += timedelta(days=1)

    bh_start, bh_end = BUSINESS_HOURS_LOCAL

    def _mark_pto_timed(s_utc, e_utc) -> None:
        """Mark PTO for each in-window weekday whose *local* working hours the block
        substantially covers — so an overnight 8–12h block (which overlaps the working
        day by ~0) isn't mistaken for a day off (#pto-overnight)."""
        d = s_utc.astimezone(tz).date()
        last = e_utc.astimezone(tz).date()
        while d <= last:
            if window_start.date() <= d < window_end.date() and d.weekday() < 5:
                w0 = datetime(d.year, d.month, d.day, bh_start, tzinfo=tz).astimezone(UTC)
                w1 = datetime(d.year, d.month, d.day, bh_end, tzinfo=tz).astimezone(UTC)
                overlap = (min(e_utc, w1) - max(s_utc, w0)).total_seconds()
                if overlap >= PTO_WORKDAY_OVERLAP_MIN_S:
                    pto_weekdays.add(d)
            d += timedelta(days=1)

    for ev in cal.walk("VEVENT"):
        ds = ev.get("DTSTART")
        if ds is None:
            continue
        start_raw = ds.dt
        de = ev.get("DTEND")
        end_raw = de.dt if de is not None else None

        # All-day events (date, not datetime) → PTO; DTEND is exclusive.
        if not isinstance(start_raw, datetime):
            end_date = end_raw if (end_raw and not isinstance(end_raw, datetime)) \
                else start_raw + timedelta(days=1)
            _mark_pto(start_raw, end_date)
            continue

        start = (start_raw if start_raw.tzinfo else start_raw.replace(tzinfo=UTC))
        end = (end_raw if (end_raw and end_raw.tzinfo) else
               (end_raw.replace(tzinfo=UTC) if end_raw else start))
        s_utc, e_utc = start.astimezone(UTC), end.astimezone(UTC)
        if e_utc <= window_start or s_utc >= window_end:
            continue
        dur = (e_utc - s_utc).total_seconds()
        clip_s, clip_e = max(s_utc, window_start), min(e_utc, window_end)

        if dur >= PTO_MIN_S:
            # Any block covering a full local working day is a day off — a single 24h
            # block (the team's day-off convention), a multi-day vacation span, or an
            # 8h "out" block. _mark_pto_timed keeps only the weekdays it actually covers,
            # so overnight holds (which overlap the working day by ~0) don't count.
            _mark_pto_timed(s_utc, e_utc)
            continue
        if dur <= MEETING_MAX_S:
            meetings.append((clip_s, clip_e))  # only ≤1h blocks are "busy" meetings
        elif SD_MIN_S <= dur <= SD_MAX_S:
            # Weekday in the event's own (local) time — "their particular day".
            sd_by_week.setdefault(start.isocalendar()[:2], start.strftime("%a"))
        # >1h blockers (between-shift holds) are neither busy nor counted vs open.

    busy_s = _merge_seconds(meetings)
    pto_s = len(pto_weekdays) * WORKDAY_S
    capacity = _weekday_count(window_start, window_end) * WORKDAY_S
    open_s = max(0, capacity - busy_s)
    sd_days = tuple(sorted(set(sd_by_week.values()), key=_WEEKDAYS.index))
    # The specific PTO dates in-window, oldest first — used to list a person's PTO
    # across this + next week on their card (#pto-card).
    pto_days = tuple(d.strftime("%a %b %d") for d in sorted(pto_weekdays))
    return CalendarAvail(
        busy_seconds=busy_s, open_seconds=open_s, pto_seconds=pto_s,
        sd_days=sd_days, has_data=True, pto_days=pto_days,
    )
