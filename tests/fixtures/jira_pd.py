"""Respx-backed mock of the Jira + PagerDuty read surface for integration tests.

``install(respx_mock, scenario)`` wires a single GET dispatcher that answers the
exact endpoints the clients call, driven by an in-memory ``Scenario``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx


def issue(
    key: str,
    *,
    status: str = "In Progress",
    assignee: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    summary: str | None = None,
    sprint_id: int | None = None,
    changelog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": summary or f"{key} summary",
            "status": {"name": status},
            "priority": {"name": priority} if priority else None,
            "labels": labels or [],
            "assignee": {"emailAddress": assignee} if assignee else None,
            "sprint": {"id": sprint_id} if sprint_id else None,
        },
        "changelog": {"histories": changelog or []},
    }


@dataclass
class Scenario:
    boards: dict[str, int] = field(default_factory=dict)          # project_key -> board id
    sprints: dict[int, dict[str, Any]] = field(default_factory=dict)  # board id -> sprint
    sprint_issues: dict[int, list[dict]] = field(default_factory=dict)  # sprint id -> issues
    search_issues: list[dict] = field(default_factory=list)
    comments: dict[str, list[dict]] = field(default_factory=dict)
    worklogs: dict[str, list[dict]] = field(default_factory=dict)
    users: list[dict] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)
    log_entries: dict[str, list[dict]] = field(default_factory=dict)
    ical_text: str | None = None
    fail_jira: bool = False  # when True, Jira endpoints return 500 (outage simulation)


_BOARD_SPRINT = re.compile(r"/rest/agile/1\.0/board/(\d+)/sprint")
_SPRINT_ISSUE = re.compile(r"/rest/agile/1\.0/sprint/(\d+)/issue")
_ISSUE_COMMENT = re.compile(r"/rest/api/3/issue/([^/]+)/comment")
_ISSUE_WORKLOG = re.compile(r"/rest/api/3/issue/([^/]+)/worklog")
_PD_LOGS = re.compile(r"/incidents/([^/]+)/log_entries")


def install(respx_mock, scenario: Scenario) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        params = request.url.params

        if host == "warthogs.atlassian.net":
            return _jira(path, params, scenario)
        if host == "api.pagerduty.com":
            return _pagerduty(path, scenario)
        if path.endswith(".ics"):
            return httpx.Response(200, text=scenario.ical_text or "")
        return httpx.Response(404, json={"error": f"unmocked {host}{path}"})

    respx_mock.route(method="GET").mock(side_effect=handler)


def _jira(path: str, params, scenario: Scenario) -> httpx.Response:
    if scenario.fail_jira:
        return httpx.Response(500, json={"error": "simulated outage"})
    if path == "/rest/agile/1.0/board":
        bid = scenario.boards.get(params.get("projectKeyOrId"))
        values = [{"id": bid}] if bid is not None else []
        return httpx.Response(200, json={"values": values})

    if m := _BOARD_SPRINT.match(path):
        sprint = scenario.sprints.get(int(m.group(1)))
        return httpx.Response(200, json={"values": [sprint] if sprint else []})

    if m := _SPRINT_ISSUE.match(path):
        issues = scenario.sprint_issues.get(int(m.group(1)), [])
        return httpx.Response(200, json={"issues": issues, "total": len(issues)})

    if path == "/rest/api/3/search/jql":
        # Enhanced search: single page, no nextPageToken (matches client paging).
        return httpx.Response(200, json={"issues": scenario.search_issues})

    if m := _ISSUE_COMMENT.match(path):
        return httpx.Response(200, json={"comments": scenario.comments.get(m.group(1), [])})

    if m := _ISSUE_WORKLOG.match(path):
        return httpx.Response(200, json={"worklogs": scenario.worklogs.get(m.group(1), [])})

    return httpx.Response(404, json={"error": f"unmocked jira {path}"})


def _pagerduty(path: str, scenario: Scenario) -> httpx.Response:
    if path == "/users":
        return httpx.Response(200, json={"users": scenario.users, "more": False})
    if path == "/incidents":
        return httpx.Response(200, json={"incidents": scenario.incidents, "more": False})
    if m := _PD_LOGS.match(path):
        return httpx.Response(
            200, json={"log_entries": scenario.log_entries.get(m.group(1), []), "more": False}
        )
    return httpx.Response(404, json={"error": f"unmocked pagerduty {path}"})
