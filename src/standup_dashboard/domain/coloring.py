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
| Project | green (only ISDB is kept)                        | yellow           |
| OFF     | red                                              | —                |

Non-assigned touches: PVG/BVG green; GEN/Project/OFF red.
FR-017: a Done (Success) ticket is always green, regardless of role.
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
    """Whether an *assigned* (non-Done) ticket is a distraction for ``role`` (#86).

    These tickets are regrouped under Distractors instead of To Do / WIP:
      * BVG: any ticket that is NOT Highest and NOT a ``[PR/MP Review]``.
      * Project: any ticket that is NOT ISDB.
    Done (Success) tickets are never distractions — finished work is a success.
    """
    if ticket.group is TicketGroup.SUCCESS:
        return False
    if role is Role.BVG:
        return not (ticket.is_highest or ticket.is_pr_mp_review)
    if role is Role.PROJECT:
        return not ticket.is_isdb
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
    # FR-017 — Success is always green.
    if group is TicketGroup.SUCCESS or ticket.group is TicketGroup.SUCCESS:
        return Color.GREEN

    # Role-based distraction: Project flags yellow, everyone else red (#86).
    if role_distractor:
        return Color.YELLOW if role is Role.PROJECT else Color.RED

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
