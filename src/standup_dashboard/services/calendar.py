"""Calendar availability from a free/busy iCal feed (#cal).

The public Google iCal feed exposes only opaque "Busy" blocks (no titles,
attendees, or types), so events are classified **purely by duration**:

  * all-day or > 8h  → PTO (excluded from busy; its weekdays drop from capacity)
  * ~4h              → SD time (one per ISO week; its weekday is marked)
  * everything else  → busy (meetings + blockers + the SD block)

``busy`` = merged wall-clock of all non-PTO blocks; ``open`` = capacity
(40h/week, minus PTO weekdays) − busy. Pure over the feed text + a window so it
is deterministically unit-testable (mirrors ``services/oncall.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from icalendar import Calendar

from ..domain.models import CalendarAvail

WEEKLY_CAPACITY_H = 40
PTO_THRESHOLD_S = 8 * 3600
SD_MIN_S = int(3.5 * 3600)   # a "4h" SD block, with tolerance
SD_MAX_S = int(4.5 * 3600)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


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
    ical_text: str, window_start: datetime, window_end: datetime
) -> CalendarAvail:
    """Busy/open/PTO/SD for the pulse window from a free/busy iCal feed."""
    cal = Calendar.from_ical(ical_text)
    busy: list[tuple[datetime, datetime]] = []
    pto_weekdays: set = set()
    sd_by_week: dict = {}  # (iso-year, iso-week) → weekday abbrev of its 4h block

    def _mark_pto(d0, d1) -> None:  # [d0, d1) over dates, weekdays only, in-window
        d = d0
        while d < d1:
            if window_start.date() <= d < window_end.date() and d.weekday() < 5:
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

        if dur > PTO_THRESHOLD_S:
            _mark_pto(clip_s.date(), clip_e.date() + timedelta(days=1))
            continue
        busy.append((clip_s, clip_e))
        if SD_MIN_S <= dur <= SD_MAX_S:
            # Weekday in the event's own (local) time — "their particular day".
            sd_by_week.setdefault(start.isocalendar()[:2], start.strftime("%a"))

    busy_s = _merge_seconds(busy)
    pto_s = len(pto_weekdays) * 8 * 3600
    weeks = (window_end - window_start).days / 7
    capacity = int(WEEKLY_CAPACITY_H * 3600 * weeks) - pto_s
    open_s = max(0, capacity - busy_s)
    sd_days = tuple(sorted(set(sd_by_week.values()), key=_WEEKDAYS.index))
    return CalendarAvail(
        busy_seconds=busy_s, open_seconds=open_s, pto_seconds=pto_s,
        sd_days=sd_days, has_data=True,
    )
