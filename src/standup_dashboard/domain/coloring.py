"""Pure role × ticket → color matrix and role-based grouping rules (#86).

No I/O, no globals — exhaustively unit-tested in ``tests/unit/test_coloring.py``.
These rules supersede the original spec matrix and the BVG strict-mode toggle.

Assigned, non-Done tickets (after role-based reclassification, see
``is_role_distractor``):

| Role    | kept-assigned color                              | role distraction |
|---------|--------------------------------------------------|------------------|
| PVG     | red (tickets are a distraction from alerts)      | —                |
| BVG     | green (only Highest / [PR/MP Review] are kept)    | red              |
| GEN     | green iff ISReq Highest/ps5, else red            | —                |
| Project | green (only ISDB is kept)                        | red              |
| OFF     | red                                              | —                |

Non-assigned touches: PVG/BVG green; GEN/Project/OFF red.
FR-017: a Done (Success) ticket is green — except for a Project engineer, whose
non-ISDB work is an off-task RED distraction even when completed.
PVG alerts (resolved → green Success, ack → yellow WIP) are surfaced in the
panel builder, which is the general case for all roles.
"""

from __future__ import annotations

from .models import Color, Role, Ticket, TicketGroup

_NON_ASSIGNED: dict[Role, Color] = {
    Role.PVG: Color.GREEN,
    Role.BVG: Color.GREEN,
    Role.GEN: Color.RED,
    Role.PROJECT: Color.RED,
    Role.OFF: Color.RED,
}


def is_role_distractor(role: Role, ticket: Ticket) -> bool:
    """Whether an assigned ticket is a distraction for ``role`` (#86).

    These tickets are regrouped under Distractors instead of To Do / WIP / Success:
      * Project: any ticket that is NOT ISDB — even when Done. A Project engineer
        should only work ISDB, so a completed ISReq is still off-task, not a
        success (overrides the FR-017 Success-green rule for this role).
      * BVG: any non-Done ticket that is NOT Highest and NOT a ``[PR/MP Review]``.
      * PVG: any non-Done ticket in Jira status "In Review".
    For BVG/PVG, finished (Success) work is a success, never a distraction.
    """
    # Project: non-ISDB is off-task regardless of status (incl. Done/Success).
    if role is Role.PROJECT:
        return not ticket.is_isdb
    if ticket.group is TicketGroup.SUCCESS:
        return False
    if role is Role.BVG:
        return not (ticket.is_highest or ticket.is_pr_mp_review)
    if role is Role.PVG:
        return (ticket.status or "").strip().lower() == "in review"
    return False


def ticket_color(
    role: Role,
    ticket: Ticket,
    *,
    assigned: bool,
    group: TicketGroup | None = None,
    role_distractor: bool = False,
) -> Color:
    """Resolve a ticket's color for an engineer with ``role``.

    ``assigned`` is True when the ticket is assigned to the engineer in the
    active sprint (including a role distraction, which is still assigned).
    ``role_distractor`` marks an assigned ticket reclassified as a distraction
    by the engineer's role (#86). ``group`` carries the precomputed status group
    so the FR-017 Success-green override applies.
    """
    # Role-based distraction takes precedence over the Success-green rule: for a
    # Project engineer a completed ISReq is still an off-task RED distraction.
    # PVG distractions stay yellow; everyone else (Project, BVG) red (#86 / #..).
    if role_distractor:
        return Color.YELLOW if role is Role.PVG else Color.RED

    # FR-017 — Success is always green for non-distractor tickets.
    if group is TicketGroup.SUCCESS or ticket.group is TicketGroup.SUCCESS:
        return Color.GREEN

    if not assigned:
        return _NON_ASSIGNED[role]

    if role is Role.OFF:
        return Color.RED

    # Assigned tickets that survived reclassification are "kept" work.
    if role is Role.BVG:
        return Color.GREEN if (ticket.is_highest or ticket.is_pr_mp_review) else Color.RED

    if role is Role.PROJECT:
        return Color.GREEN if ticket.is_isdb else Color.RED

    if role is Role.PVG:
        return Color.RED

    if role is Role.GEN:
        is_priority = ticket.is_highest or ticket.has_ps5_blockers
        return Color.GREEN if (ticket.is_isreq and is_priority) else Color.RED

    return Color.RED  # defensive; all roles handled above


# Alerts are coloured by the handler's role — whether handling alerts is on-task
# for them — independent of acked/resolved/age (#143): PVG green (alert duty is
# their job), BVG/GEN yellow (secondary), Project/OFF red (off-task).
_ALERT_ROLE_COLOR: dict[Role, Color] = {
    Role.PVG: Color.GREEN,
    Role.BVG: Color.YELLOW,
    Role.GEN: Color.YELLOW,
    Role.PROJECT: Color.RED,
    Role.OFF: Color.RED,
}


def alert_color(role: Role) -> Color:
    """Colour of an alert handled by an engineer with ``role`` (#143).

    Single source of truth for both the detail panel and the colour legend. The
    panel still groups alerts (resolved → Success, open → WIP, GEN → Distractors);
    only the colour is role-based.
    """
    return _ALERT_ROLE_COLOR.get(role, Color.RED)


# --- Alert counts-table cell coloring (green / yellow / red bands) ----------
# Volume columns (Ack, Total) are judged against an on-call "fatigue" cap that
# the caller scales by the row's span (weekday / weekend / pulse) AND the number
# of selected regions — more engineers on call ⇒ a higher healthy ceiling. The
# resolve rate and the MTTR/MTTA means are *rates*, not volumes, so they share
# fixed thresholds and are NOT scaled by region count.
ALERT_RES_GREEN = 0.80            # resolved ≥80% of acked → keeping pace (green)
ALERT_RES_YELLOW = 0.50           # 50–79% slipping (yellow); <50% backlog (red)
ALERT_MTTA_GREEN_S = 5 * 60       # ≤5m ack latency is healthy
ALERT_MTTA_YELLOW_S = 15 * 60     # 5–15m slipping; >15m alerts go unnoticed (red)
ALERT_MTTR_GREEN_S = 30 * 60      # ≤30m is a tidy resolve
ALERT_MTTR_YELLOW_S = 2 * 60 * 60  # 30m–2h acceptable; >2h painful (red)


def count_level(count: int, green_cap: int) -> Color | None:
    """Volume band: green ≤ cap, yellow ≤ 2×cap, red beyond (None if no cap)."""
    if green_cap <= 0:
        return None
    if count <= green_cap:
        return Color.GREEN
    if count <= 2 * green_cap:
        return Color.YELLOW
    return Color.RED


def resolve_rate_level(resolved: int, acked: int) -> Color | None:
    """Resolved-vs-acked band; None when there was nothing to acknowledge."""
    if acked <= 0:
        return None
    rate = resolved / acked
    if rate >= ALERT_RES_GREEN:
        return Color.GREEN
    if rate >= ALERT_RES_YELLOW:
        return Color.YELLOW
    return Color.RED


def _duration_level(seconds: float | None, green_max: float, yellow_max: float) -> Color | None:
    """Lower-is-better band: green ≤ green_max, yellow ≤ yellow_max, else red."""
    if seconds is None:
        return None
    if seconds <= green_max:
        return Color.GREEN
    if seconds <= yellow_max:
        return Color.YELLOW
    return Color.RED


def mttr_level(seconds: float | None) -> Color | None:
    """Ack→resolve mean band (#140): ≤30m green, 30m–2h yellow, >2h red."""
    return _duration_level(seconds, ALERT_MTTR_GREEN_S, ALERT_MTTR_YELLOW_S)


def mtta_level(seconds: float | None) -> Color | None:
    """Trigger→ack mean band (#140): ≤5m green, 5–15m yellow, >15m red."""
    return _duration_level(seconds, ALERT_MTTA_GREEN_S, ALERT_MTTA_YELLOW_S)


ALERT_WIP_GREEN_DAYS = 2     # ≤2 days in progress is healthy
ALERT_WIP_YELLOW_DAYS = 5    # 3–5 days is ageing; >5 days is stale (red)


def wip_age_level(age_seconds: float | None) -> Color | None:
    """Aging-WIP band (#147): ≤2d green, 3–5d yellow, >5d red; None if not WIP."""
    if age_seconds is None:
        return None
    days = age_seconds / 86400
    if days <= ALERT_WIP_GREEN_DAYS:
        return Color.GREEN
    if days <= ALERT_WIP_YELLOW_DAYS:
        return Color.YELLOW
    return Color.RED


def pr_mp_review_level(review_new: int, closed: int) -> Color | None:
    """Closed PR/MP vs reviews requested (#141): are we keeping up with reviews?

    ``review_new`` is the New PR/MP Review count, ``closed`` the Closed PR/MP
    count. Green when we closed at least as many as came in (closing *more* is
    fine — another region may have left one), yellow when exactly one is left
    behind, red when two or more are. Neutral when there was no PR/MP activity.
    """
    if review_new == 0 and closed == 0:
        return None
    deficit = review_new - closed
    if deficit <= 0:
        return Color.GREEN
    if deficit == 1:
        return Color.YELLOW
    return Color.RED
