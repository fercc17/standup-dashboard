"""Read-only guard: external clients issue only GET requests (FR-027) — T016."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from standup_dashboard.clients.base import ReadOnlyClient
from standup_dashboard.clients.github import GitHubClient
from standup_dashboard.clients.jira import JiraClient
from standup_dashboard.clients.pagerduty import PagerDutyClient

_MUTATING = ("post", "put", "delete", "patch")


def test_base_exposes_no_mutating_helpers():
    for name in _MUTATING:
        assert not hasattr(ReadOnlyClient, name)
        assert not hasattr(ReadOnlyClient, f"_{name}")


@pytest.mark.parametrize("client_cls", [JiraClient, PagerDutyClient, GitHubClient])
def test_clients_expose_no_mutating_methods(client_cls):
    public = {n for n in dir(client_cls) if not n.startswith("_")}
    for verb in _MUTATING:
        assert not any(verb in name.lower() for name in public), verb


@respx.mock
async def test_jira_client_only_gets():
    respx.get(url__regex=r".*").mock(
        return_value=httpx.Response(200, json={"values": [], "issues": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="https://example.atlassian.net") as hc:
        jira = JiraClient(hc)
        await jira.active_sprint("ISDB")
        await jira.search('project = ISDB')
        await jira.comments("ISDB-1")
        await jira.worklogs("ISDB-1")

    assert respx.calls.call_count > 0
    for call in respx.calls:
        assert call.request.method == "GET", call.request.url


@respx.mock
async def test_pagerduty_client_only_gets():
    respx.get(url__regex=r".*").mock(
        return_value=httpx.Response(
            200, json={"users": [], "incidents": [], "log_entries": [], "more": False}
        )
    )
    async with httpx.AsyncClient(base_url="https://api.pagerduty.com") as hc:
        pd = PagerDutyClient(hc)
        await pd.list_users()
        await pd.find_user_by_email("e@example.com")
        await pd.incidents(
            datetime(2026, 6, 1, tzinfo=UTC),
            datetime(2026, 6, 11, tzinfo=UTC),
        )
        await pd.log_entries("PINC1")

    assert respx.calls.call_count > 0
    for call in respx.calls:
        assert call.request.method == "GET", call.request.url
