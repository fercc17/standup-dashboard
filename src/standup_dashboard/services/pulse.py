"""Active-sprint ("pulse") resolution per project (FR-012) — T022.

A ticket is "in pulse" iff it belongs to its own project's active sprint. The
sprint start/end define the per-day rows of the counts table (US3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..clients.jira import JiraClient
from ..domain.models import Pulse


def parse_jira_dt(value: str | None) -> datetime | None:
    """Parse a Jira ISO-8601 timestamp into an aware UTC datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _sprint_to_pulse(project_key: str, sprint: dict[str, Any]) -> Pulse | None:
    start = parse_jira_dt(sprint.get("startDate"))
    end = parse_jira_dt(sprint.get("endDate"))
    if start is None or end is None:
        return None
    return Pulse(
        project_key=project_key,
        sprint_id=int(sprint["id"]),
        name=sprint.get("name", ""),
        start=start,
        end=end,
        state=sprint.get("state", "active"),
    )


async def resolve_pulses(jira: JiraClient, project_keys: tuple[str, ...]) -> list[Pulse]:
    """Resolve the active sprint for each project, skipping projects with none."""
    pulses: list[Pulse] = []
    for key in project_keys:
        sprint = await jira.active_sprint(key)
        if sprint is None:
            continue
        pulse = _sprint_to_pulse(key, sprint)
        if pulse is not None:
            pulses.append(pulse)
    return pulses
