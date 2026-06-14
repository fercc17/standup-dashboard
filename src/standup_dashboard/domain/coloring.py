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


def alert_color(
    role: Role, *, resolved: bool, recent: bool, is_management: bool = False
) -> tuple[Color, TicketGroup]:
    """Colour + group for an engineer's own PagerDuty alert (#143).

    The general case (all roles): resolved → green Success; acknowledged and
    recent (≤24h) → yellow WIP; acknowledged but stale (>24h, still open) → red
    WIP. For a GEN engineer alerts are a distraction from ISReq work, so they go
    under Distractors instead — resolved yellow, unresolved red.

    Single source of truth for both the detail panel and the colour legend.
    """
    if role is Role.GEN and not is_management:
        return (Color.YELLOW if resolved else Color.RED), TicketGroup.DISTRACTORS
    if resolved:
        return Color.GREEN, TicketGroup.SUCCESS
    if recent:
        return Color.YELLOW, TicketGroup.WIP
    return Color.RED, TicketGroup.WIP
