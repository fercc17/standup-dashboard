"""Read-only GitHub client for the "GH PRs" card line (#173).

Counts each engineer's pull-request activity via the Search API: PRs they
created, merged, or touched, plus PRs they reviewed. Returns counts for three
windows at once — the whole pulse, the last 24h, and today (the engineer's local
day) — from a **single** set of queries (it reads the returned items' timestamps
and buckets locally), so the extra columns don't add to the rate-limited load.

Strictly read-only (FR-027): only GET. Auth is a personal access token from
``secrets/github_token.txt``. The Search endpoint rate-limits aggressively, so
``_do_search`` retries on a 403/429, honouring ``Retry-After`` / reset headers.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

import httpx

from ..domain.models import GitHubPRStats
from .base import ReadOnlyClient

_API = "https://api.github.com"
_MAX_RETRIES = 5
_MAX_BACKOFF_S = 120.0
_PAGE = 100  # one page is plenty: 24h items are the most-recent slice (sorted desc)


def make_async_client(token: str, *, base_url: str = _API) -> httpx.AsyncClient:
    """Build an httpx client with GitHub bearer auth and the recommended headers."""
    return httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )


def _rate_limit_delay(resp: httpx.Response, attempt: int) -> float | None:
    """Seconds to wait before retrying a rate-limited Search response, or None."""
    if resp.status_code not in (403, 429):
        return None
    retry_after = resp.headers.get("retry-after")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), _MAX_BACKOFF_S)
    reset = resp.headers.get("x-ratelimit-reset")
    if resp.headers.get("x-ratelimit-remaining") == "0" and reset and reset.isdigit():
        return max(0.0, min(float(reset) - time.time(), _MAX_BACKOFF_S))
    if "secondary rate limit" in resp.text.lower():
        return min(2.0 ** attempt, _MAX_BACKOFF_S)
    return None


def _item_dt(item: dict, field: str) -> datetime | None:
    """Aware datetime for a search item's relevant timestamp (None if absent)."""
    raw = ((item.get("pull_request") or {}).get("merged_at")
           if field == "merged_at" else item.get(field))
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class GitHubClient(ReadOnlyClient):
    async def _do_search(self, params: dict) -> dict:
        """Run a Search-API issues query, retrying on the search rate limit.
        Non-rate-limit errors (e.g. a 422 for an unsearchable login) raise."""
        for attempt in range(_MAX_RETRIES + 1):
            resp = await self._client.get("/search/issues", params=params)
            if attempt < _MAX_RETRIES:
                delay = _rate_limit_delay(resp, attempt)
                if delay is not None:
                    await asyncio.sleep(delay)
                    continue
            resp.raise_for_status()
            return resp.json()
        return {}

    async def _search_count(self, qualifiers: list[str]) -> int:
        """``total_count`` only (used by the rate-limit retry test)."""
        data = await self._do_search({"q": " ".join(qualifiers), "per_page": 1})
        return int(data.get("total_count", 0))

    async def _bucketed(
        self, qualifiers: list[str], ts_field: str, cutoff: datetime, today: datetime
    ) -> tuple[int, int, int]:
        """(total, count ≥ ``cutoff``, count ≥ ``today``) for one query.

        One page of up-to-100 items sorted by most-recent activity. Both the 24h
        and the (always more recent, ⊆ 24h) today slices are the newest items, so
        they're on the first page — counted locally from the same fetch, no extra
        queries."""
        data = await self._do_search({
            "q": " ".join(qualifiers), "per_page": _PAGE, "sort": "updated", "order": "desc",
        })
        total = int(data.get("total_count", 0))
        recent = today_n = 0
        for it in data.get("items", []):
            dt = _item_dt(it, ts_field)
            if dt is None:
                continue
            if dt >= cutoff:
                recent += 1
            if dt >= today:
                today_n += 1
        return total, recent, today_n

    async def pr_activity(
        self, login: str, *, since: date, until: date,
        cutoff: datetime, today: datetime, org: str = "",
    ) -> tuple[GitHubPRStats, GitHubPRStats, GitHubPRStats]:
        """PR activity for ``login``, returned as (pulse, last-24h, today) stats.

        Pulse counts cover ``[since, until]``; the 24h counts are the subset whose
        timestamp is ≥ ``cutoff``; today is the further subset ≥ ``today`` (the
        engineer's local midnight). Four sequential queries (created / merged /
        updated / reviewed), org-scoped when given — all three windows bucketed
        locally from the same fetch, so the query count is unchanged.
        """
        scope = [f"org:{org}"] if org else []
        window = f"{since.isoformat()}..{until.isoformat()}"

        async def m(extra: list[str], ts_field: str) -> tuple[int, int, int]:
            return await self._bucketed(["is:pr", *scope, *extra], ts_field, cutoff, today)

        ct, c24, ctd = await m([f"author:{login}", f"created:{window}"], "created_at")
        mt, m24, mtd = await m([f"author:{login}", f"merged:{window}"], "merged_at")
        ut, u24, utd = await m([f"author:{login}", f"updated:{window}"], "updated_at")
        rt, r24, rtd = await m([f"reviewed-by:{login}", f"updated:{window}"], "updated_at")
        return (
            GitHubPRStats(created=ct, merged=mt, updated=ut, reviewed=rt),
            GitHubPRStats(created=c24, merged=m24, updated=u24, reviewed=r24),
            GitHubPRStats(created=ctd, merged=mtd, updated=utd, reviewed=rtd),
        )
