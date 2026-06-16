"""Read-only GitHub client for the "GH PRs" card line (#173).

Counts each engineer's pull-request activity within the current pulse window via
the Search API: PRs they created, merged, or touched, plus PRs they reviewed.
Strictly read-only (FR-027): only GET, no mutation. Auth is a personal access
token (or fine-grained token) from ``secrets/github_token.txt`` with read access
to the org's repositories.

The Search API rate-limits aggressively (a low primary cap plus a burst-based
secondary limit), so ``_search_count`` retries on a 403/429 rate-limit response,
honouring ``Retry-After`` / ``X-RateLimit-Reset`` before backing off.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

import httpx

from ..domain.models import GitHubPRStats
from .base import ReadOnlyClient

_API = "https://api.github.com"
_MAX_RETRIES = 5
_MAX_BACKOFF_S = 120.0


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
    """Seconds to wait before retrying a rate-limited Search response, or None if
    the response isn't a rate-limit we should retry."""
    if resp.status_code not in (403, 429):
        return None
    retry_after = resp.headers.get("retry-after")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), _MAX_BACKOFF_S)
    reset = resp.headers.get("x-ratelimit-reset")
    if resp.headers.get("x-ratelimit-remaining") == "0" and reset and reset.isdigit():
        return max(0.0, min(float(reset) - time.time(), _MAX_BACKOFF_S))
    # Secondary limits often omit the headers — back off exponentially anyway.
    if "secondary rate limit" in resp.text.lower():
        return min(2.0 ** attempt, _MAX_BACKOFF_S)
    return None


class GitHubClient(ReadOnlyClient):
    async def _search_count(self, qualifiers: list[str]) -> int:
        """``total_count`` for a Search-API issues query, retrying on the search
        rate limit. Non-rate-limit errors (e.g. a 422 for an unsearchable login)
        raise so the caller can isolate that engineer."""
        params = {"q": " ".join(qualifiers), "per_page": 1}
        for attempt in range(_MAX_RETRIES + 1):
            resp = await self._client.get("/search/issues", params=params)
            if attempt < _MAX_RETRIES:
                delay = _rate_limit_delay(resp, attempt)
                if delay is not None:
                    await asyncio.sleep(delay)
                    continue
            resp.raise_for_status()
            return int(resp.json().get("total_count", 0))
        return 0

    async def pr_pulse_stats(
        self, login: str, *, since: date, until: date, org: str = ""
    ) -> GitHubPRStats:
        """PR activity for ``login`` over the inclusive ``[since, until]`` window.

        Four Search-API counts (run sequentially to avoid bursting the per-login
        rate limit), scoped to ``org`` when given:
          * created  — ``author:login created:since..until``
          * merged   — ``author:login merged:since..until``
          * updated  — ``author:login updated:since..until``
          * reviewed — ``reviewed-by:login updated:since..until`` (GitHub can't
            filter reviews by date; the pulse ``updated`` window is the proxy).
        """
        scope = [f"org:{org}"] if org else []
        window = f"{since.isoformat()}..{until.isoformat()}"

        async def count(*extra: str) -> int:
            return await self._search_count(["is:pr", *scope, *extra])

        return GitHubPRStats(
            created=await count(f"author:{login}", f"created:{window}"),
            merged=await count(f"author:{login}", f"merged:{window}"),
            updated=await count(f"author:{login}", f"updated:{window}"),
            reviewed=await count(f"reviewed-by:{login}", f"updated:{window}"),
        )
