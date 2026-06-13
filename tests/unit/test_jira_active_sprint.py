"""active_sprint: a pinned board is authoritative (kanban → no sprint, no fallback)."""

from __future__ import annotations

import httpx
import respx

from standup_dashboard import config
from standup_dashboard.clients.jira import JiraClient

BASE = "https://example.atlassian.net"


@respx.mock
async def test_pinned_kanban_board_yields_no_sprint_without_discovery():
    pinned = config.PROJECT_BOARDS["ISDB"]  # the pinned ISDB (kanban) board
    # The pinned board's sprint endpoint 400s like a real kanban board.
    sprint = respx.get(url__regex=rf".*/board/{pinned}/sprint.*").mock(
        return_value=httpx.Response(400, json={"errorMessages": ["no sprint support"]})
    )
    # If discovery were (wrongly) attempted, it would surface a foreign scrum sprint.
    discovery = respx.get(url__regex=r".*/board\?.*").mock(
        return_value=httpx.Response(200, json={"values": [{"id": 999, "type": "scrum"}]})
    )

    async with httpx.AsyncClient(base_url=BASE) as hc:
        result = await JiraClient(hc).active_sprint("ISDB")

    assert result is None             # kanban project has no sprint pulse
    assert sprint.called              # the pinned board was consulted
    assert not discovery.called       # and we did NOT fall back to discovery


@respx.mock
async def test_pinned_scrum_board_returns_its_active_sprint():
    pinned = config.PROJECT_BOARDS["ISReq"]
    respx.get(url__regex=rf".*/board/{pinned}/sprint.*").mock(
        return_value=httpx.Response(200, json={"values": [{"id": 34046, "name": "S"}]})
    )
    async with httpx.AsyncClient(base_url=BASE) as hc:
        result = await JiraClient(hc).active_sprint("ISReq")
    assert result is not None and result["id"] == 34046
