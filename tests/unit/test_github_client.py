"""GitHubClient: per-pulse + last-24h + today PR activity via the Search API (#173)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import respx

from standup_dashboard.clients.github import GitHubClient

BASE = "https://api.github.com"
SINCE = date(2026, 6, 8)
UNTIL = date(2026, 6, 21)
CUTOFF = datetime(2026, 6, 15, tzinfo=UTC)  # rolling-24h boundary
TODAY = datetime(2026, 6, 16, tzinfo=UTC)   # local-midnight boundary (⊆ 24h)

# Three items per query: today (≥ TODAY), within-24h-but-not-today (≥ CUTOFF only),
# and old (neither) — so the three buckets get distinct counts.
_ITEMS = [
    {"created_at": "2026-06-16T10:00:00Z", "updated_at": "2026-06-16T10:00:00Z",
     "pull_request": {"merged_at": "2026-06-16T10:00:00Z"}},   # today
    {"created_at": "2026-06-15T12:00:00Z", "updated_at": "2026-06-15T12:00:00Z",
     "pull_request": {"merged_at": "2026-06-15T12:00:00Z"}},   # 24h, not today
    {"created_at": "2026-06-09T10:00:00Z", "updated_at": "2026-06-09T10:00:00Z",
     "pull_request": {"merged_at": "2026-06-09T10:00:00Z"}},   # old
]


@respx.mock
async def test_pr_activity_pulse_24h_and_today():
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        if "reviewed-by:octocat" in q:
            total = 5
        elif "created:" in q:
            total = 3
        elif "merged:" in q:
            total = 2
        elif "updated:" in q:
            total = 4
        else:
            total = 0
        return httpx.Response(200, json={"total_count": total, "items": _ITEMS})

    route = respx.get(url__regex=rf"{BASE}/search/issues.*").mock(side_effect=handler)
    async with httpx.AsyncClient(base_url=BASE) as hc:
        pulse, day, today = await GitHubClient(hc).pr_activity(
            "octocat", since=SINCE, until=UNTIL, cutoff=CUTOFF, today=TODAY, org="canonical")

    # Pulse uses total_count; 24h = items ≥ CUTOFF (2 of 3); today = items ≥ TODAY (1 of 3).
    assert (pulse.created, pulse.merged, pulse.updated, pulse.reviewed) == (3, 2, 4, 5)
    assert (day.created, day.merged, day.updated, day.reviewed) == (2, 2, 2, 2)
    assert (today.created, today.merged, today.updated, today.reviewed) == (1, 1, 1, 1)

    queries = [c.request.url.params["q"] for c in route.calls]
    assert len(queries) == 4   # same query count — all three windows bucketed locally
    for q in queries:
        assert "is:pr" in q
        assert "org:canonical" in q
        assert "2026-06-08..2026-06-21" in q
    assert any("author:octocat" in q and "created:" in q for q in queries)
    assert any("reviewed-by:octocat" in q for q in queries)


@respx.mock
async def test_pr_activity_without_org_omits_org_qualifier():
    route = respx.get(url__regex=rf"{BASE}/search/issues.*").mock(
        return_value=httpx.Response(200, json={"total_count": 0, "items": []}))
    async with httpx.AsyncClient(base_url=BASE) as hc:
        pulse, day, today = await GitHubClient(hc).pr_activity(
            "octocat", since=SINCE, until=UNTIL, cutoff=CUTOFF, today=TODAY)
    assert pulse.created == 0 and day.created == 0 and today.created == 0
    assert all("org:" not in c.request.url.params["q"] for c in route.calls)


@respx.mock
async def test_search_count_retries_on_secondary_rate_limit(monkeypatch):
    import standup_dashboard.clients.github as gh

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(gh.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, json={"message": "You have exceeded a secondary rate limit"})
        return httpx.Response(200, json={"total_count": 7})

    respx.get(url__regex=rf"{BASE}/search/issues.*").mock(side_effect=handler)
    async with httpx.AsyncClient(base_url=BASE) as hc:
        n = await GitHubClient(hc)._search_count(["is:pr", "author:octocat"])

    assert n == 7
    assert calls["n"] == 2
