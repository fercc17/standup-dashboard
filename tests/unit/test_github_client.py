"""GitHubClient: per-pulse PR activity via the Search API (#173)."""

from __future__ import annotations

from datetime import date

import httpx
import respx

from standup_dashboard.clients.github import GitHubClient

BASE = "https://api.github.com"
SINCE = date(2026, 6, 8)
UNTIL = date(2026, 6, 21)


@respx.mock
async def test_pr_pulse_stats_counts_each_metric():
    # Distinct total_count per metric so the four queries are distinguishable.
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        if "reviewed-by:octocat" in q:
            n = 5
        elif "created:" in q:
            n = 3
        elif "merged:" in q:
            n = 2
        elif "updated:" in q:
            n = 4
        else:
            n = 0
        return httpx.Response(200, json={"total_count": n})

    route = respx.get(url__regex=rf"{BASE}/search/issues.*").mock(side_effect=handler)
    async with httpx.AsyncClient(base_url=BASE) as hc:
        stats = await GitHubClient(hc).pr_pulse_stats(
            "octocat", since=SINCE, until=UNTIL, org="canonical"
        )

    assert (stats.created, stats.merged, stats.updated, stats.reviewed) == (3, 2, 4, 5)
    queries = [c.request.url.params["q"] for c in route.calls]
    assert len(queries) == 4
    # Every query is PR-only, org-scoped, and uses the inclusive pulse window.
    for q in queries:
        assert "is:pr" in q
        assert "org:canonical" in q
        assert "2026-06-08..2026-06-21" in q
    # created/merged/updated are author-based; reviews use reviewed-by.
    assert any("author:octocat" in q and "created:" in q for q in queries)
    assert any("author:octocat" in q and "merged:" in q for q in queries)
    assert any("author:octocat" in q and "updated:" in q for q in queries)
    assert any("reviewed-by:octocat" in q for q in queries)


@respx.mock
async def test_pr_pulse_stats_without_org_omits_org_qualifier():
    route = respx.get(url__regex=rf"{BASE}/search/issues.*").mock(
        return_value=httpx.Response(200, json={"total_count": 0})
    )
    async with httpx.AsyncClient(base_url=BASE) as hc:
        stats = await GitHubClient(hc).pr_pulse_stats("octocat", since=SINCE, until=UNTIL)

    assert stats.created == 0
    assert all("org:" not in c.request.url.params["q"] for c in route.calls)


@respx.mock
async def test_search_count_retries_on_secondary_rate_limit(monkeypatch):
    # A 403 secondary-rate-limit response is retried (after a backoff sleep we
    # stub out), then succeeds — it must not surface as an error.
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
    assert calls["n"] == 2  # one rate-limited attempt, one success
