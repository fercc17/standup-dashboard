"""Ticket classification into To Do / WIP / Success / Distractors (FR-013) — T024.

Everything shown is scoped to the current pulse. For an engineer E:
  * To Do / WIP / Success = tickets assigned to E that are **in scope** — in one
    of the pulse's active sprints, or fresh **untriaged ISReq** intake not yet
    sprinted (so brand-new customer requests surface) — grouped by status. Work
    parked with no sprint (or in another, non-active sprint) is the engineer's
    backlog, not this pulse, so it is not shown.
  * A board can run several concurrent active sprints — its own plus a shared
    cross-team one (e.g. ISDB's board carries the shared "IS Pulse" sprint that
    originates on the ISReq board AND ISDB's own sprint). A ticket is in pulse
    when it belongs to **any** of them.
  * An ISDB ticket in the Done category counts as a Success only if it genuinely
    completed this pulse (a real transition into Done within ``pulse_window``).
    ISDB tickets that were Rejected/dropped (Done category but no done date) are
    not shown — matching the board, which has no Rejected column.
  * An ISReq ticket Done *before* this pulse is dropped even while Jira still
    carries it in an active sprint (a completed sprint isn't de-activated the
    moment the next one starts, so last pulse's closed work can linger). A Done
    ISReq with no recorded done date is kept — finished work is not hidden merely
    because its transition date is unknown (#prev-pulse-leak).
  * Success also includes tickets E **touched** that are **Done and in the
    active pulse** but assigned to someone else (#74).
  * Distractors = in-pulse tickets E touched but is not assigned to (they got
    pulled into a teammate's current-sprint work). Touches outside the active
    pulse are not shown.

The ``[PR/MP Review]`` ISReq prefix is already surfaced on ``Ticket`` via
``is_bvg_review`` (FR-015); detection lives on the model.
"""

from __future__ import annotations

from datetime import date

from ..domain.models import Ticket, TicketGroup, TouchEvent


def in_pulse(ticket: Ticket, active_sprint_ids: set[int]) -> bool:
    """True iff the ticket belongs to one of the pulse's active sprints.

    ``active_sprint_ids`` is the set of every active sprint across the projects'
    boards (a board can run its own sprint plus a shared cross-team one), so a
    ticket counts as this-pulse work when it sits in any of them.
    """
    return ticket.sprint_id is not None and ticket.sprint_id in active_sprint_ids


def in_scope(
    ticket: Ticket,
    active_sprint_ids: set[int],
    pulse_window: tuple[date, date] | None = None,
) -> bool:
    """Whether an assigned ticket counts as this-pulse work for its engineer.

    In scope when any of:
      * the ticket is in one of the pulse's active sprints — except a Done ticket
        that belongs to a prior pulse: an ISDB Done counts only if it genuinely
        completed this pulse (a real Done transition within ``pulse_window``; a
        Rejected/dropped ISDB ticket with no done date is not shown), and an
        ISReq Done is dropped if it completed *before* the pulse started;
      * it is fresh untriaged ISReq intake not yet sprinted (a new customer
        request the team still needs to triage).

    Backlog parked with no sprint or in another, non-active sprint is out of
    scope. ``pulse_window`` is ``(start, end_exclusive)`` region-local dates.
    """
    if in_pulse(ticket, active_sprint_ids):
        if ticket.group is TicketGroup.SUCCESS:
            if ticket.is_isdb:
                return _done_this_pulse(ticket, pulse_window)
            # ISReq (and any other project): keep unless it was Done in a prior
            # pulse but is still pinned to an active sprint (#prev-pulse-leak).
            return not _done_before_pulse(ticket, pulse_window)
        return True
    if (
        ticket.is_isreq
        and ticket.sprint_id is None
        and (ticket.status or "").strip().lower() == "untriaged"
    ):
        return True
    return False


def _done_this_pulse(ticket: Ticket, pulse_window: tuple[date, date] | None) -> bool:
    """True iff the ticket transitioned into Done within the pulse window."""
    return (
        pulse_window is not None
        and ticket.is_done_date is not None
        and pulse_window[0] <= ticket.is_done_date < pulse_window[1]
    )


def _done_before_pulse(ticket: Ticket, pulse_window: tuple[date, date] | None) -> bool:
    """True iff the ticket transitioned into Done *before* the pulse started.

    Catches prior-pulse Done work that Jira still lists in an active sprint (a
    completed sprint isn't de-activated the instant the next one starts). Unlike
    ``_done_this_pulse`` this keeps a Done ticket whose done date is unknown — we
    only drop work we can positively date to an earlier pulse, so a current Done
    with a missing transition date is never wrongly hidden."""
    return (
        pulse_window is not None
        and ticket.is_done_date is not None
        and ticket.is_done_date < pulse_window[0]
    )


def classify_for_engineer(
    email: str,
    tickets: list[Ticket],
    touches: list[TouchEvent],
    active_sprint_ids: set[int],
    pulse_window: tuple[date, date] | None = None,
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
        if t.assignee_email == email and in_scope(t, active_sprint_ids, pulse_window):
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
        if not in_pulse(ticket, active_sprint_ids):
            continue
        if ticket.group is TicketGroup.SUCCESS:
            # Don't credit a teammate's ticket that was Done in a prior pulse but
            # is still pinned to an active sprint (#prev-pulse-leak).
            if not _done_before_pulse(ticket, pulse_window):
                groups[TicketGroup.SUCCESS].append(ticket)
        else:
            groups[TicketGroup.DISTRACTORS].append(ticket)

    return groups
