"""Read-only PagerDuty client (contracts/pagerduty.md).

Exposes only the GET read surface: user lookup (email→identity, FR-005a),
incidents in a window, and per-incident log entries (who acked/resolved and
when). Auth via ``Authorization: Token token=<...>``. No method mutates
PagerDuty (FR-027).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .base import ReadOnlyClient

_PD_BASE = "https://api.pagerduty.com"
_PAGE = 100


def make_async_client(token: str, *, base_url: str = _PD_BASE) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
        timeout=30.0,
    )


class PagerDutyClient(ReadOnlyClient):
    async def list_users(self) -> list[dict[str, Any]]:
        """All users (id, name, email) for roster identity resolution."""
        return await self._paginate("/users", "users", params={"include[]": "contact_methods"})

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        data = await self._get_json("/users", params={"query": email})
        for user in data.get("users", []):
            if (user.get("email") or "").lower() == email.lower():
                return user
        return None

    async def incidents(
        self,
        since: datetime,
        until: datetime,
        *,
        team_ids: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Acknowledged + resolved incidents in the window, optionally team-scoped."""
        params: dict[str, Any] = {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "time_zone": "UTC",
            "statuses[]": ["acknowledged", "resolved"],
        }
        if team_ids:
            params["team_ids[]"] = list(team_ids)
        return await self._paginate("/incidents", "incidents", params=params)

    async def open_incident_count(
        self, team_ids: list[str] | tuple[str, ...] | None = None
    ) -> int:
        """Number of still-open (triggered + acknowledged) incidents, team-scoped.

        This is the live figure behind the 'Ongoing alerts' summary link, queried
        directly rather than derived from accumulated ack/resolve events (which can
        strand an auto-resolved incident on ACK; #stale-ack). ``total=true`` makes
        PagerDuty return the match count, so a single 1-row page suffices."""
        params: dict[str, Any] = {
            "statuses[]": ["triggered", "acknowledged"],
            "total": "true",
            "limit": 1,
        }
        if team_ids:
            params["team_ids[]"] = list(team_ids)
        data = await self._get_json("/incidents", params=params)
        return int(data.get("total") or 0)

    async def log_entries(self, incident_id: str) -> list[dict[str, Any]]:
        return await self._paginate(
            f"/incidents/{incident_id}/log_entries", "log_entries",
            params={"is_overview": "false"},
        )

    async def _paginate(
        self, url: str, key: str, *, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params = {**params, "offset": offset, "limit": _PAGE}
            data = await self._get_json(url, params=page_params)
            items = data.get(key, [])
            out.extend(items)
            offset += len(items)
            if not items or not data.get("more"):
                break
        return out
