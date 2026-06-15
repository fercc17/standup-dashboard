"""Aging work-in-progress: tickets sitting In Progress too long (#147).

For the selected region(s)' members, lists every ticket currently in the WIP
group (In Progress / In Review) with how long it has sat in its current WIP
streak (``Ticket.wip_since`` → now), newest-stuck last. Reads stored ticket data
— the changelog needed for ``wip_since`` is already fetched, so no new calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .. import config
from ..domain.coloring import wip_age_level
from ..domain.models import Color, TicketGroup, Ticket, format_duration


# Statuses excluded from Aging WIP even when their Jira statusCategory is
# "In Progress": a Blocked ticket isn't actively being worked, so it shouldn't
# age as work-in-progress (#147 follow-up).
_EXCLUDED_WIP_STATUSES = frozenset({"blocked"})


@dataclass
class AgingRow:
    key: str
    title: str
    assignee: str
    status: str
    age_label: str            # human duration, e.g. "6d 4h" or "—"
    age_seconds: float | None
    url: str
    level: Color | None       # green / yellow / red band, None if unknown age


def build_aging_wip(
    tickets: list[Ticket], members: set[str], now: datetime
) -> list[AgingRow]:
    """WIP tickets owned by ``members``, most-aged first (#147)."""
    rows: list[AgingRow] = []
    for t in tickets:
        if t.assignee_email not in members or t.group is not TicketGroup.WIP:
            continue
        if (t.status or "").strip().lower() in _EXCLUDED_WIP_STATUSES:
            continue  # Blocked isn't active work — don't age it as WIP (#147)
        age = t.wip_age_seconds(now)
        eng = config.ENGINEERS_BY_EMAIL.get(t.assignee_email)
        rows.append(AgingRow(
            key=t.id,
            title=t.title,
            assignee=eng.name if eng else (t.assignee_email or "—"),
            status=t.status,
            age_label=format_duration(age),
            age_seconds=age,
            url=config.jira_browse_url(t.id),
            level=wip_age_level(age),
        ))
    # Most-aged first; tickets with unknown age (no wip_since) sort last.
    rows.sort(key=lambda r: (r.age_seconds is None, -(r.age_seconds or 0)))
    return rows
