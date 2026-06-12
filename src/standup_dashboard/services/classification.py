"""Ticket classification into To Do / WIP / Success / Distractors (FR-013) — T024.

For an engineer E:
  * To Do / WIP / Success = tickets assigned to E **and in pulse** (member of
    its own project's active sprint), grouped by status.
  * Success also includes tickets E **touched** that are **Done and in the
    active pulse**, even if unassigned (#74) — finishing someone's ticket is a
    success, not a distraction.
  * Distractors = other tickets E touched during the pulse but is not assigned
    to in this sprint, or that belong to a different sprint.

The ``[PR/MP Review]`` ISReq prefix is already surfaced on ``Ticket`` via
``is_bvg_review`` (FR-015); detection lives on the model.
"""

from __future__ import annotations

from ..domain.models import Ticket, TicketGroup, TouchEvent


def in_pulse(ticket: Ticket, pulse_sprint_ids: dict[str, int]) -> bool:
    """True iff the ticket belongs to its own project's active sprint."""
    sprint_id = pulse_sprint_ids.get(ticket.project_key)
    return sprint_id is not None and ticket.sprint_id == sprint_id


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
        if t.assignee_email == email and in_pulse(t, pulse_sprint_ids):
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
        # A ticket E touched that is Done and in the active pulse counts as a
        # Success even when it isn't assigned to E (#74); everything else they
        # only touched is a Distractor.
        if ticket.group is TicketGroup.SUCCESS and in_pulse(ticket, pulse_sprint_ids):
            groups[TicketGroup.SUCCESS].append(ticket)
        else:
            groups[TicketGroup.DISTRACTORS].append(ticket)

    return groups
