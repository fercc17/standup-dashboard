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
        """Resolve the active sprint for a project.

        A pinned board id in config is **authoritative**: if it is a kanban board
        with no active sprint (returns 400 on the sprint endpoint), the project
        simply has no sprint pulse — we do NOT fall back to board discovery, which
        would otherwise adopt another project's shared scrum sprint (e.g. ISDB on
        kanban board 1400 wrongly picking up ISReq's sprint). Discovery is only a
        safety net for projects with no pinned board.
        """
        pinned = config.PROJECT_BOARDS.get(project_key)
        if pinned is not None:
            return await self._first_active_sprint([pinned])

        boards = await self._get_json(
            f"{_AGILE}/board", params={"projectKeyOrId": project_key}
        )
        discovered = [
            b["id"] for b in boards.get("values", [])
            if (b.get("type") or "scrum") == "scrum"
        ]
        return await self._first_active_sprint(discovered)

    async def _first_active_sprint(self, board_ids: list[int]) -> dict[str, Any] | None:
        for board_id in board_ids:
            try:
                sprints = await self._get_json(
                    f"{_AGILE}/board/{board_id}/sprint", params={"state": "active"}
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    continue  # kanban board — no sprints; other errors are real
                raise
            values = sprints.get("values", [])
            if values:
                return values[0]
        return None

    async def sprint_issues(self, sprint_id: int) -> list[dict[str, Any]]:
        """All issues in a sprint, with changelog expanded, paginated."""
        return await self._paginate(
            f"{_AGILE}/sprint/{sprint_id}/issue",
            params={
                "fields": "summary,status,priority,labels,assignee,reporter,sprint,created,updated",
                "expand": "changelog",
            },
        )

    async def search(self, jql: str, *, expand_changelog: bool = True) -> list[dict[str, Any]]:
        """Run a JQL search via the enhanced ``/search/jql`` endpoint (token paging).

        The legacy ``/rest/api/3/search`` GET endpoint was removed by Atlassian
        (HTTP 410); this uses its replacement, which paginates by nextPageToken.
        """
        params: dict[str, Any] = {
            "jql": jql,
            "fields": "summary,status,priority,labels,assignee,reporter,created",
            "maxResults": _PAGE,
        }
        if expand_changelog:
            params["expand"] = "changelog"

        out: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            page = {**params, **({"nextPageToken": token} if token else {})}
            data = await self._get_json(f"{_API}/search/jql", params=page)
            out.extend(data.get("issues", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return out

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
