"""Read-only weekend on-call iCal client (contracts/pagerduty.md §3) — T048.

Fetches the PagerDuty schedule iCal feed (URL from ``secrets/pagerduty_ical_url.txt``)
as text; parsing lives in ``services/oncall.py``. GET only (FR-027).
"""

from __future__ import annotations

import httpx

from .base import ReadOnlyClient


def make_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0, headers={"Accept": "text/calendar, text/plain"})


class ICalClient(ReadOnlyClient):
    async def fetch(self, url: str) -> str:
        return await self._get_text(url)
