"""Calendar availability: duration-based classification + busy/open (#cal)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from standup_dashboard import config
from standup_dashboard.services.calendar import compute_availability
from standup_dashboard.services.fetch import _fetch_calendar

WS = datetime(2026, 6, 8, tzinfo=UTC)
WE = datetime(2026, 6, 22, tzinfo=UTC)
NOW = datetime(2026, 6, 16, tzinfo=UTC)


def _ics(events) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//t//"]
    for i, (s, e, allday) in enumerate(events):
        lines += ["BEGIN:VEVENT", f"UID:{i}"]
        if allday:
            lines += [f"DTSTART;VALUE=DATE:{s}", f"DTEND;VALUE=DATE:{e}"]
        else:
            lines += [f"DTSTART:{s}", f"DTEND:{e}"]
        lines += ["SUMMARY:Busy", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def test_classifies_and_computes():
    ics = _ics([
        ("20260608T100000Z", "20260608T103000Z", False),  # 30m meeting (Mon)
        ("20260609T100000Z", "20260609T120000Z", False),  # 2h blocker (Tue)
        ("20260610T130000Z", "20260610T170000Z", False),  # 4h SD (Wed)
        ("20260611", "20260612", True),                   # all-day PTO (Thu)
        ("20260601T100000Z", "20260601T110000Z", False),  # before window → ignored
    ])
    a = compute_availability(ics, WS, WE)
    assert a.has_data
    assert a.busy_seconds == 30 * 60                  # only the ≤1h meeting; blocker+SD excluded
    assert a.pto_seconds == 8 * 3600
    assert a.sd_days == ("Wed",)
    # open = 40h/week capacity − busy; blockers and PTO don't reduce it.
    assert a.open_seconds == 40 * 3600 * 2 - 30 * 60


def test_long_block_is_pto_not_busy():
    a = compute_availability(_ics([("20260608T080000Z", "20260608T180000Z", False)]), WS, WE)
    assert a.busy_seconds == 0          # 10h block → PTO, never busy
    assert a.pto_seconds == 8 * 3600
    assert a.pto_days == ("Mon Jun 08",)  # the specific PTO date is listed (#pto-card)


def test_full_day_block_is_pto():
    """The team marks a day off as a ≥24h 'Busy' block covering the local day — so a
    24h block makes that weekday PTO (#cal-off), and it's never counted as busy."""
    a = compute_availability(
        _ics([("20260608T060000Z", "20260609T060000Z", False)]), WS, WE)  # full Mon
    assert a.pto_days == ("Mon Jun 08",)
    assert a.pto_seconds == 8 * 3600
    assert a.busy_seconds == 0          # a day off is not "busy" meeting time


def test_multi_day_block_marks_each_weekday_pto():
    """A multi-day block (e.g. a short vacation) → every weekday it covers is PTO."""
    a = compute_availability(
        _ics([("20260608T060000Z", "20260611T060000Z", False)]), WS, WE)  # Mon–Wed
    assert a.pto_days == ("Mon Jun 08", "Tue Jun 09", "Wed Jun 10")
    assert a.pto_seconds == 3 * 8 * 3600


def test_eight_hour_block_counts_as_pto():
    a = compute_availability(
        _ics([("20260608T090000Z", "20260608T170000Z", False)]), WS, WE)  # exactly 8h
    assert a.pto_seconds == 8 * 3600
    assert a.pto_days == ("Mon Jun 08",)


def test_overnight_block_is_not_pto_in_local_tz():
    """A long block that sits *overnight* in the engineer's timezone is a personal
    hold, not a day off — it must not read as PTO (#pto-overnight)."""
    from zoneinfo import ZoneInfo
    mx = ZoneInfo("America/Mexico_City")  # UTC−6, like AMER
    # 02:00–11:45 UTC = 20:00–05:45 Mexico: ~10h overnight, 0 overlap with 09:00–17:00.
    overnight = compute_availability(
        _ics([("20260608T020000Z", "20260608T114500Z", False)]), WS, WE, mx)
    assert overnight.pto_days == () and overnight.pto_seconds == 0
    # 15:00–23:00 UTC = 09:00–17:00 Mexico: covers the local working day → real PTO.
    daytime = compute_availability(
        _ics([("20260608T150000Z", "20260608T230000Z", False)]), WS, WE, mx)
    assert daytime.pto_days == ("Mon Jun 08",) and daytime.pto_seconds == 8 * 3600


def test_overlapping_meetings_merged():
    a = compute_availability(_ics([
        ("20260608T100000Z", "20260608T110000Z", False),
        ("20260608T103000Z", "20260608T113000Z", False),  # overlaps → union 90m
    ]), WS, WE)
    assert a.busy_seconds == 90 * 60


def test_compute_windows_matches_per_window():
    """Parsing the feed once for several windows matches per-window parsing — the
    fetch path relies on this to avoid re-parsing a multi-MB feed (#cal)."""
    from standup_dashboard.services.calendar import compute_availability_windows
    ics = _ics([
        ("20260608T100000Z", "20260608T103000Z", False),  # Mon 30m
        ("20260616T140000Z", "20260616T150000Z", False),  # Tue (today) 60m
    ])
    day = (datetime(2026, 6, 16, tzinfo=UTC), datetime(2026, 6, 17, tzinfo=UTC))
    pulse_w, day_w = compute_availability_windows(ics, [(WS, WE), day])
    assert pulse_w.busy_seconds == compute_availability(ics, WS, WE).busy_seconds
    assert day_w.busy_seconds == compute_availability(ics, *day).busy_seconds == 60 * 60


async def test_fetch_calendar_inert_by_default(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_ENABLED", False)
    res = await _fetch_calendar(NOW)
    assert res.avail == {} and res.ok is True


@respx.mock
async def test_fetch_calendar_skips_unreachable(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_ENABLED", True)
    monkeypatch.setattr(config, "all_roster_emails", lambda: ["a@x.com", "b@x.com"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "a%40x.com" in str(request.url):
            return httpx.Response(200, text=_ics([
                ("20260616T100000Z", "20260616T103000Z", False)]))
        return httpx.Response(404)  # b's calendar isn't public

    respx.get(url__regex=r"https://calendar\.google\.com/.*").mock(side_effect=handler)
    res = await _fetch_calendar(NOW)
    assert set(res.avail) == {"a@x.com"}      # unreachable b skipped, not fatal
    assert res.avail["a@x.com"].busy_seconds == 30 * 60


@respx.mock
async def test_fetch_calendar_today_is_local_day(monkeypatch):
    """The 24H/today number is the engineer's *local* calendar day, not the UTC
    day — so for AMER (Mexico City, UTC−6) a UTC Tuesday must not bleed Monday
    evening in nor drop Tuesday evening out (#cal)."""
    amer = "fernando.carrillo.castro@canonical.com"  # AMER → America/Mexico_City
    monkeypatch.setattr(config, "CALENDAR_ENABLED", True)
    monkeypatch.setattr(config, "all_roster_emails", lambda: [amer])
    # now = Tue 2026-06-16 12:00 UTC = Tue 06:00 Mexico City. Local Tuesday window
    # is [Tue 06:00 UTC, Wed 06:00 UTC).
    now = datetime(2026, 6, 16, 12, tzinfo=UTC)

    respx.get(url__regex=r"https://calendar\.google\.com/.*").mock(
        return_value=httpx.Response(200, text=_ics([
            ("20260616T010000Z", "20260616T013000Z", False),  # Mon 19:00 MX → excluded
            ("20260616T150000Z", "20260616T153000Z", False),  # Tue 09:00 MX → 30m
            ("20260617T020000Z", "20260617T030000Z", False),  # Tue 20:00 MX → 60m
        ])))
    res = await _fetch_calendar(now)
    av = res.avail[amer]
    assert av.busy_today_seconds == 90 * 60          # Tue morning + Tue evening, local
    assert av.open_today_seconds == 8 * 3600 - 90 * 60  # 8h weekday capacity − busy
