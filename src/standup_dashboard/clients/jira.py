"""Read-only Jira Cloud client (contracts/jira.md).

Exposes only the GET read surface the dashboard needs: active sprint per
project, sprint issues (with changelog), JQL search, comments, worklogs, and
the daily-counts searches (US3). HTTP Basic auth uses the account email plus
the token from ``secrets/jira_token.txt``. No method mutates Jira (FR-027).
"""

from __future__ import annotations

from typing import Any

import httpx

from .. import config
from .base import ReadOnlyClient

_AGILE = "/rest/agile/1.0"
_API = "/rest/api/3"
_PAGE = 50


def make_async_client(token: str, *, base_url: str = config.JIRA_BASE_URL) -> httpx.AsyncClient:
    """Build an httpx client with Jira Cloud Basic auth (email + API token)."""
    return httpx.AsyncClient(
        base_url=base_url,
        auth=(config.JIRA_ACCOUNT_EMAIL, token),
        headers={"Accept": "application/json"},
        timeout=30.0,
    )


class JiraClient(ReadOnlyClient):
    async def active_sprint(self, project_key: str) -> dict[str, Any] | None:
        """Resolve the active sprint for a project (first board, first active sprint)."""
        boards = await self._get_json(
            f"{_AGILE}/board", params={"projectKeyOrId": project_key}
        )
        for board in boards.get("values", []):
            sprints = await self._get_json(
                f"{_AGILE}/board/{board['id']}/sprint", params={"state": "active"}
            )
            values = sprints.get("values", [])
            if values:
                return values[0]
        return None

    async def sprint_issues(self, sprint_id: int) -> list[dict[str, Any]]:
        """All issues in a sprint, with changelog expanded, paginated."""
        return await self._paginate(
            f"{_AGILE}/sprint/{sprint_id}/issue",
            params={
                "fields": "summary,status,priority,labels,assignee,sprint,created",
                "expand": "changelog",
            },
        )

    async def search(self, jql: str, *, expand_changelog: bool = True) -> list[dict[str, Any]]:
        """Run a JQL search, paginated, optionally expanding changelog."""
        params: dict[str, Any] = {
            "jql": jql,
            "fields": "summary,status,priority,labels,assignee,created",
        }
        if expand_changelog:
            params["expand"] = "changelog"
        return await self._paginate(f"{_API}/search", params=params)

    async def search_count(self, jql: str) -> int:
        """Total matches for a JQL query without fetching every issue."""
        data = await self._get_json(
            f"{_API}/search", params={"jql": jql, "maxResults": 0}
        )
        return int(data.get("total", 0))

    async def comments(self, issue_key: str) -> list[dict[str, Any]]:
        data = await self._get_json(f"{_API}/issue/{issue_key}/comment")
        return data.get("comments", [])

    async def worklogs(self, issue_key: str) -> list[dict[str, Any]]:
        data = await self._get_json(f"{_API}/issue/{issue_key}/worklog")
        return data.get("worklogs", [])

    async def _paginate(self, url: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            page_params = {**params, "startAt": start, "maxResults": _PAGE}
            data = await self._get_json(url, params=page_params)
            issues = data.get("issues", data.get("values", []))
            out.extend(issues)
            total = data.get("total")
            start += len(issues)
            if not issues or (total is not None and start >= total):
                break
        return out
