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
    capacity = 40 * 3600 * 2 - 8 * 3600
    occupied = (30 * 60) + (2 * 3600) + (4 * 3600)    # meeting + blocker + SD all remove "open"
    assert a.open_seconds == capacity - occupied


def test_long_block_is_pto_not_busy():
    a = compute_availability(_ics([("20260608T080000Z", "20260608T180000Z", False)]), WS, WE)
    assert a.busy_seconds == 0          # 10h block → PTO, never busy
    assert a.pto_seconds == 8 * 3600


def test_overlapping_meetings_merged():
    a = compute_availability(_ics([
        ("20260608T100000Z", "20260608T110000Z", False),
        ("20260608T103000Z", "20260608T113000Z", False),  # overlaps → union 90m
    ]), WS, WE)
    assert a.busy_seconds == 90 * 60


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
