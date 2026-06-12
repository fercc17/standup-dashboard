"""Exhaustive color-matrix tests (FR-016/017) — T014."""

from __future__ import annotations

import pytest

from standup_dashboard.domain.coloring import ticket_color
from standup_dashboard.domain.models import Color, Role, Ticket


def mk(project="ISReq", status="In Progress", priority=None, labels=None, title="x"):
    return Ticket(
        id="ISReq-1", project_key=project, title=title, status=status,
        priority=priority, labels=labels or [],
    )


# (role, project, priority, labels, assigned, strict) -> expected color
ASSIGNED_CASES = [
    # PVG
    (Role.PVG, "ISReq", None, [], True, False, Color.RED),
    (Role.PVG, "ISDB", None, [], True, False, Color.RED),
    # BVG non-strict: all assigned ISReq green; ISDB red
    (Role.BVG, "ISReq", None, [], True, False, Color.GREEN),
    (Role.BVG, "ISReq", "Highest", [], True, False, Color.GREEN),
    (Role.BVG, "ISDB", None, [], True, False, Color.RED),
    # BVG strict: green iff Highest or ps5-blockers, else yellow
    (Role.BVG, "ISReq", None, [], True, True, Color.YELLOW),
    (Role.BVG, "ISReq", "Highest", [], True, True, Color.GREEN),
    (Role.BVG, "ISReq", None, ["ps5-blockers"], True, True, Color.GREEN),
    (Role.BVG, "ISDB", None, [], True, True, Color.RED),
    # GEN: green iff Highest or ps5; else red; ISDB red
    (Role.GEN, "ISReq", None, [], True, False, Color.RED),
    (Role.GEN, "ISReq", "Highest", [], True, False, Color.GREEN),
    (Role.GEN, "ISReq", None, ["ps5-blockers"], True, False, Color.GREEN),
    (Role.GEN, "ISDB", "Highest", [], True, False, Color.RED),
    # Project: ISReq red, ISDB green
    (Role.PROJECT, "ISReq", None, [], True, False, Color.RED),
    (Role.PROJECT, "ISDB", None, [], True, False, Color.GREEN),
    # OFF: everything red
    (Role.OFF, "ISReq", "Highest", ["ps5-blockers"], True, False, Color.RED),
    (Role.OFF, "ISDB", None, [], True, False, Color.RED),
]


@pytest.mark.parametrize("role,project,priority,labels,assigned,strict,expected", ASSIGNED_CASES)
def test_assigned_matrix(role, project, priority, labels, assigned, strict, expected):
    t = mk(project=project, priority=priority, labels=labels)
    assert ticket_color(role, t, assigned=assigned, strict_mode=strict) == expected


NON_ASSIGNED_CASES = [
    (Role.PVG, Color.GREEN),
    (Role.BVG, Color.GREEN),
    (Role.GEN, Color.RED),
    (Role.PROJECT, Color.RED),
    (Role.OFF, Color.RED),
]


@pytest.mark.parametrize("role,expected", NON_ASSIGNED_CASES)
def test_non_assigned_touch(role, expected):
    t = mk(project="ISReq", priority="Highest")  # priority irrelevant when non-assigned
    assert ticket_color(role, t, assigned=False) == expected


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("assigned", [True, False])
def test_success_always_green(role, assigned):
    """FR-017: Done tickets are green for every role, assigned or not."""
    t = mk(project="ISReq", status="Done")
    assert ticket_color(role, t, assigned=assigned, strict_mode=True) == Color.GREEN
