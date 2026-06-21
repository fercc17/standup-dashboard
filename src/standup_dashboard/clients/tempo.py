"""Read-only Tempo Cloud client (#tempo-worklogs).

Tempo records worklogs under a bot author in Jira, so Jira's
``/issue/{key}/worklog`` can't tell who actually logged the time — the dashboard
otherwise has to credit it to the ticket's assignee. Tempo's own REST API does
carry the real logger (``author.accountId``) per worklog, so when a Tempo token
is configured we read worklogs here instead. GET only; never mutates (FR-027).

Auth is a Bearer token with the **Worklogs: View** scope (Tempo → Settings → API
integration). Nothing else is needed: issue id→key comes from the Jira issues we
already fetch, and accountId→email reuses the Jira identity map.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from .. import config
from .base import ReadOnlyClient

# Tempo caps page size at 1000; one request usually covers a refresh window.
_PAGE = 1000


def make_async_client(token: str, *, base_url: str = config.TEMPO_BASE_URL) -> httpx.AsyncClient:
    """Build an httpx client with Tempo Bearer auth."""
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30.0,
    )


class TempoClient(ReadOnlyClient):
    async def worklogs(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """All worklogs whose start date falls in ``[date_from, date_to]``.

        Paginates Tempo's offset/limit ``metadata.next`` until exhausted. Each
        worklog carries ``issue.id`` (numeric), ``author.accountId``,
        ``timeSpentSeconds``, ``startDate`` and ``startTime``.
        """
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get_json(
                "/worklogs",
                params={
                    "from": date_from.isoformat(),
                    "to": date_to.isoformat(),
                    "limit": _PAGE,
                    "offset": offset,
                },
            )
            results = data.get("results", []) or []
            out.extend(results)
            # Stop when the page is short or Tempo reports no next page.
            if not results or not (data.get("metadata") or {}).get("next"):
                break
            offset += len(results)
        return out
