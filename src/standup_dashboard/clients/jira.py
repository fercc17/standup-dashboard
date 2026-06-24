"""Read-only Jira Cloud client (contracts/jira.md).

Exposes only the GET read surface the dashboard needs: active sprint per
project, sprint issues (with changelog), JQL search, comments, worklogs, and
the daily-counts searches (US3). HTTP Basic auth uses the account email plus
the token from ``secrets/jira_token.txt``. No method mutates Jira (FR-027).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
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
        """The primary active sprint for a project (first one), or None."""
        sprints = await self.active_sprints(project_key)
        return sprints[0] if sprints else None

    async def active_sprints(self, project_key: str) -> list[dict[str, Any]]:
        """**All** active sprints on a project's board.

        A scrum board can run several concurrent active sprints — e.g. the ISDB
        board carries the shared cross-team sprint plus ISDB's own — so callers
        fetch issues from every one to avoid missing sprint tickets.

        A pinned board id in config is authoritative; only when a project has no
        pinned board do we fall back to discovering its scrum boards (kanban
        boards have no sprints and 400 on the sprint endpoint).
        """
        pinned = config.PROJECT_BOARDS.get(project_key)
        if pinned is not None:
            return await self._active_sprints_on([pinned])

        boards = await self._get_json(
            f"{_AGILE}/board", params={"projectKeyOrId": project_key}
        )
        discovered = [
            b["id"] for b in boards.get("values", [])
            if (b.get("type") or "scrum") == "scrum"
        ]
        return await self._active_sprints_on(discovered)

    async def _active_sprints_on(self, board_ids: list[int]) -> list[dict[str, Any]]:
        sprints: list[dict[str, Any]] = []
        for board_id in board_ids:
            try:
                data = await self._get_json(
                    f"{_AGILE}/board/{board_id}/sprint", params={"state": "active"}
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    continue  # kanban board — no sprints; other errors are real
                raise
            sprints.extend(data.get("values", []))
        return sprints

    async def sprint_issues(self, sprint_id: int) -> list[dict[str, Any]]:
        """All issues in a sprint, with changelog expanded, paginated."""
        return await self._paginate(
            f"{_AGILE}/sprint/{sprint_id}/issue",
            params={
                "fields": "summary,status,priority,labels,assignee,reporter,sprint,created,"
                          "updated,timeoriginalestimate,timespent",
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
            "fields": "summary,status,priority,labels,assignee,reporter,created,"
                      "timeoriginalestimate,timespent",
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

    async def count(self, jql: str) -> int:
        """Number of issues matching ``jql`` (e.g. a saved filter via ``filter=39785``).

        The enhanced ``/search/jql`` endpoint dropped the ``total`` field and Jira's
        approximate-count endpoint is POST-only — which the read-only base forbids
        (GET surface only, FR-027). So page the matches with no changelog and a single
        small field and tally them. The open-work filters return small sets (tens of
        issues), so this is a page or two each.
        """
        params: dict[str, Any] = {"jql": jql, "fields": "summary", "maxResults": _PAGE}
        total = 0
        token: str | None = None
        while True:
            page = {**params, **({"nextPageToken": token} if token else {})}
            data = await self._get_json(f"{_API}/search/jql", params=page)
            total += len(data.get("issues", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return total

    async def account_ids_for(self, emails: Iterable[str]) -> dict[str, str]:
        """Map Jira ``accountId`` → email for each given roster email.

        Recovers attribution for accounts whose email Atlassian hides: the
        ``emailAddress`` field is omitted from issue/user objects for a private
        email-visibility profile, so attribution by email alone silently drops
        those engineers' tickets and touches. ``/user/search`` still resolves an
        email to its ``accountId`` (the returned ``emailAddress`` is blank), so
        we recover the link. One bounded-concurrent lookup per email; a per-email
        failure is skipped rather than aborting the whole map (#priv-email).
        """
        sem = asyncio.Semaphore(10)

        async def _one(email: str) -> tuple[str, str] | None:
            async with sem:
                try:
                    users = await self._get_json(
                        f"{_API}/user/search", params={"query": email}
                    )
                except Exception:  # noqa: BLE001 — skip this email, keep the rest
                    return None
            # Prefer an exact email match; private-email accounts return a blank
            # emailAddress, so fall back to the single/first hit for the query.
            chosen = next(
                (u for u in users if (u.get("emailAddress") or "").lower() == email.lower()),
                users[0] if users else None,
            )
            acct = (chosen or {}).get("accountId")
            return (acct, email) if acct else None

        pairs = await asyncio.gather(*(_one(e) for e in emails))
        return {pair[0]: pair[1] for pair in pairs if pair}

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
