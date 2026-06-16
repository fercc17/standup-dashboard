"""_fetch_github: per-login isolation + inert-without-config (#173)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from standup_dashboard import config
from standup_dashboard.services.fetch import _fetch_github
from standup_dashboard.settings import Secrets

_SECRETS = Secrets("jira", "pd", "ical", github_token="ghp_x")
NOW = datetime(2026, 6, 16, tzinfo=UTC)  # inside an anchored pulse


@respx.mock
async def test_one_bad_login_does_not_zero_out_the_rest(monkeypatch):
    # An unsearchable handle 422s on the Search API; that must not abort the whole
    # fetch — the other engineers' stats still come through.
    monkeypatch.setattr(config, "GITHUB_ORG", "testorg")
    monkeypatch.setattr(config, "github_logins",
                        lambda: {"good@x.com": "gooduser", "bad@x.com": "baduser"})

    def handler(request: httpx.Request) -> httpx.Response:
        if "baduser" in request.url.params["q"]:
            return httpx.Response(422, json={"message": "Validation Failed"})
        return httpx.Response(200, json={"total_count": 2})

    respx.get(url__regex=r"https://api\.github\.com/search/issues.*").mock(side_effect=handler)

    res = await _fetch_github(_SECRETS, NOW)
    assert set(res.pr_stats) == {"good@x.com"}   # bad login skipped, not fatal
    assert res.pr_stats["good@x.com"].created == 2
    assert res.pr_stats["good@x.com"].reviewed == 2
    assert res.ok is True


async def test_inert_without_token(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_ORG", "testorg")
    monkeypatch.setattr(config, "github_logins", lambda: {"a@x.com": "a"})
    res = await _fetch_github(Secrets("jira", "pd", "ical", github_token=None), NOW)
    assert res.pr_stats == {} and res.ok is True


async def test_inert_without_org(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_ORG", "")
    monkeypatch.setattr(config, "github_logins", lambda: {"a@x.com": "a"})
    res = await _fetch_github(_SECRETS, NOW)
    assert res.pr_stats == {} and res.ok is True
