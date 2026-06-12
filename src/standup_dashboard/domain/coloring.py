"""Pure role × project → color matrix (FR-016/017).

No I/O, no globals — exhaustively unit-tested in ``tests/unit/test_coloring.py``.

Matrix (assigned tickets, non-Success):

| Role    | assigned ISReq                                  | assigned ISDB | non-assigned touch |
|---------|-------------------------------------------------|---------------|--------------------|
| PVG     | red                                             | red           | green              |
| BVG     | green (strict: green iff Highest/ps5 else yellow)| red          | green              |
| GEN     | green iff Highest/ps5, else red                 | red           | red                |
| Project | red                                             | green         | red                |
| OFF     | red                                             | red           | red                |

FR-017: a Success (Done) ticket is always green, regardless of role.
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


def ticket_color(
    role: Role,
    ticket: Ticket,
    *,
    assigned: bool,
    strict_mode: bool = False,
    group: TicketGroup | None = None,
) -> Color:
    """Resolve a ticket's color for an engineer with ``role``.

    ``assigned`` is True when the ticket is assigned to the engineer in the
    active sprint; False marks a non-assigned touch (a Distractor).
    ``group`` lets the caller pass the precomputed status group; when it (or
    the ticket status) is Success, the FR-017 green override applies.
    """
    # FR-017 — Success is always green.
    if group is TicketGroup.SUCCESS or ticket.group is TicketGroup.SUCCESS:
        return Color.GREEN

    if not assigned:
        return _NON_ASSIGNED[role]

    if role is Role.OFF:
        return Color.RED

    if ticket.is_isdb:
        return Color.GREEN if role is Role.PROJECT else Color.RED

    # Assigned ISReq from here on.
    if role in (Role.PVG, Role.PROJECT):
        return Color.RED

    is_priority = ticket.is_highest or ticket.has_ps5_blockers

    if role is Role.BVG:
        if strict_mode:
            return Color.GREEN if is_priority else Color.YELLOW
        return Color.GREEN

    if role is Role.GEN:
        return Color.GREEN if is_priority else Color.RED

    return Color.RED  # defensive; all roles handled above
