"""Ticket classification into To Do / WIP / Success / Distractors (FR-013) — T024.

Everything shown is scoped to the current pulse. For an engineer E:
  * To Do / WIP / Success = tickets assigned to E that are **in scope** —
    in the active sprint, or fresh **untriaged ISReq** intake not yet sprinted
    (so brand-new customer requests surface) — grouped by status. Assigned
    tickets that are out of scope (e.g. backlog with no/another sprint) are the
    engineer's backlog, not this pulse, so they are not shown.
  * Success also includes tickets E **touched** that are **Done and in the
    active pulse** but assigned to someone else (#74).
  * Distractors = in-pulse tickets E touched but is not assigned to (they got
    pulled into a teammate's current-sprint work). Touches outside the active
    pulse are not shown.

The ``[PR/MP Review]`` ISReq prefix is already surfaced on ``Ticket`` via
``is_bvg_review`` (FR-015); detection lives on the model.
"""

from __future__ import annotations

from ..domain.models import Ticket, TicketGroup, TouchEvent


def in_pulse(ticket: Ticket, pulse_sprint_ids: dict[str, int]) -> bool:
    """True iff the ticket belongs to its own project's active sprint."""
    sprint_id = pulse_sprint_ids.get(ticket.project_key)
    return sprint_id is not None and ticket.sprint_id == sprint_id


def in_scope(ticket: Ticket, pulse_sprint_ids: dict[str, int]) -> bool:
    """Whether an assigned ticket counts as this-pulse work for its engineer.

    In the active sprint, or fresh untriaged ISReq intake not yet sprinted (a new
    customer request the team still needs to triage). Backlog tickets parked with
    no sprint or in another sprint are out of scope.
    """
    if in_pulse(ticket, pulse_sprint_ids):
        return True
    return (
        ticket.is_isreq
        and ticket.sprint_id is None
        and (ticket.status or "").strip().lower() == "untriaged"
    )


def classify_for_engineer(
    email: str,
    tickets: list[Ticket],
    touches: list[TouchEvent],
    pulse_sprint_ids: dict[str, int],
) -> dict[TicketGroup, list[Ticket]]:
    by_id = {t.id: t for t in tickets}
    groups: dict[TicketGroup, list[Ticket]] = {
        TicketGroup.TODO: [],
        TicketGroup.WIP: [],
        TicketGroup.SUCCESS: [],
        TicketGroup.DISTRACTORS: [],
    }

    assigned_ids: set[str] = set()
    for t in tickets:
        if t.assignee_email == email and in_scope(t, pulse_sprint_ids):
            group = t.group
            if group in (TicketGroup.TODO, TicketGroup.WIP, TicketGroup.SUCCESS):
                groups[group].append(t)
                assigned_ids.add(t.id)

    touched_ids = {tc.ticket_id for tc in touches if tc.engineer_email == email}
    for tid in touched_ids:
        if tid in assigned_ids:
            continue
        ticket = by_id.get(tid)
        if ticket is None:
            continue
        # A ticket assigned to E but out of scope is their backlog, not a
        # distraction — don't show it. Only in-pulse touches of *others'*
        # tickets surface: Done → Success (#74), otherwise a Distractor.
        if ticket.assignee_email == email:
            continue
        if not in_pulse(ticket, pulse_sprint_ids):
            continue
        if ticket.group is TicketGroup.SUCCESS:
            groups[TicketGroup.SUCCESS].append(ticket)
        else:
            groups[TicketGroup.DISTRACTORS].append(ticket)

    return groups
