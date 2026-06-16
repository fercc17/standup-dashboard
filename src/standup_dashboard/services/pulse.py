"""Active-sprint ("pulse") resolution per project (FR-012) — T022.

A ticket is "in pulse" iff it belongs to its own project's active sprint. The
sprint start/end define the per-day rows of the counts table (US3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .. import config
from ..clients.jira import JiraClient
from ..domain.models import Pulse


def current_pulse(today: date) -> tuple[int, date, date]:
    """Return (pulse_number, start_monday, end_exclusive) for the pulse calendar.

    Anchored on ``config.PULSE_ANCHORS`` with a 2-week cadence (#93). Uses the
    latest anchor on/before ``today`` so a new year's anchor renumbers cleanly.

    Rollover is automatic (#144): because the pulse number is derived purely from
    the date, crossing a boundary advances it with no manual step — the counts
    table resets to the new window and ``persist_pulse_summaries`` freezes the
    just-ended pulse into pulse history (the previous-pulse gap-fill is
    replace=False, so a later refresh can't wipe a frozen pulse to zero).
    """
    anchor_date, anchor_num = max(
        (a for a in config.PULSE_ANCHORS if a[0] <= today),
        default=min(config.PULSE_ANCHORS, key=lambda a: a[0]),
        key=lambda a: a[0],
    )
    k = (today - anchor_date).days // config.PULSE_LENGTH_DAYS
    # Clamp k to 0 when today precedes the earliest anchor (no negative pulses).
    k = max(k, 0)
    start = anchor_date + timedelta(days=k * config.PULSE_LENGTH_DAYS)
    end = start + timedelta(days=config.PULSE_LENGTH_DAYS)
    return anchor_num + k, start, end


def previous_pulse(today: date) -> tuple[int, date, date]:
    """(pulse_number, start, end_exclusive) for the pulse before the current one."""
    num, start, _ = current_pulse(today)
    return num - 1, start - timedelta(days=config.PULSE_LENGTH_DAYS), start


def pulse_window(num: int) -> tuple[date, date]:
    """(start_monday, end_exclusive) for a given pulse number.

    Inverts ``current_pulse`` off the anchor grid (any anchor works — they all
    lie on the same 2-week cadence), so it resolves past pulses for backfill.
    """
    anchor_date, anchor_num = config.PULSE_ANCHORS[0]
    start = anchor_date + timedelta(days=(num - anchor_num) * config.PULSE_LENGTH_DAYS)
    return start, start + timedelta(days=config.PULSE_LENGTH_DAYS)


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
    """Resolve **every** active sprint across the projects' boards (deduped).

    A pinned scrum board can run several concurrent active sprints — its own plus
    a shared cross-team one (e.g. ISDB's board carries the shared "IS Pulse"
    sprint that originates on the ISReq board AND ISDB's own sprint). All of them
    make up the current pulse, so a ticket is this-pulse work when it belongs to
    ANY of them (see ``classification.in_pulse``). Recording only the first
    sprint per project — the old behaviour — silently dropped a board's own
    sprint, so ISDB tickets fell out of pulse scope (#172).
    """
    pulses: list[Pulse] = []
    seen: set[int] = set()
    for key in project_keys:
        for sprint in await jira.active_sprints(key):
            sid = int(sprint["id"])
            if sid in seen:
                continue
            pulse = _sprint_to_pulse(key, sprint)
            if pulse is not None:
                seen.add(sid)
                pulses.append(pulse)
    return pulses
