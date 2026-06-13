"""Role-based color matrix + reclassification tests (FR-016/017, #86) — T014."""

from __future__ import annotations

import pytest

from standup_dashboard.domain.coloring import is_role_distractor, ticket_color
from standup_dashboard.domain.models import Color, Role, Ticket


def mk(project="ISReq", status="In Progress", priority=None, labels=None, title="x"):
    return Ticket(
        id=f"{project}-1", project_key=project, title=title, status=status,
        priority=priority, labels=labels or [],
    )


# Kept-assigned coloring (role_distractor=False) -> expected color.
ASSIGNED_CASES = [
    # PVG: tickets are a distraction from alerts → red.
    (Role.PVG, mk("ISReq", priority="Highest"), Color.RED),
    (Role.PVG, mk("ISDB"), Color.RED),
    # BVG: kept work (Highest / [PR/MP Review], any project) → green; else red.
    (Role.BVG, mk("ISReq", priority="Highest"), Color.GREEN),
    (Role.BVG, mk("ISReq", title="[PR/MP Review] x"), Color.GREEN),
    (Role.BVG, mk("ISDB", priority="Highest"), Color.GREEN),
    (Role.BVG, mk("ISReq"), Color.RED),
    # GEN: green iff ISReq Highest/ps5; ISDB red.
    (Role.GEN, mk("ISReq", priority="Highest"), Color.GREEN),
    (Role.GEN, mk("ISReq", labels=["ps5-blockers"]), Color.GREEN),
    (Role.GEN, mk("ISReq"), Color.RED),
    (Role.GEN, mk("ISDB", priority="Highest"), Color.RED),
    # Project: ISDB green, ISReq red.
    (Role.PROJECT, mk("ISDB"), Color.GREEN),
    (Role.PROJECT, mk("ISReq"), Color.RED),
    # OFF: everything red.
    (Role.OFF, mk("ISReq", priority="Highest", labels=["ps5-blockers"]), Color.RED),
    (Role.OFF, mk("ISDB"), Color.RED),
]


@pytest.mark.parametrize("role,ticket,expected", ASSIGNED_CASES)
def test_assigned_matrix(role, ticket, expected):
    assert ticket_color(role, ticket, assigned=True) == expected


# Role-based distractions (#86): PVG flags yellow, Project / BVG flag red.
@pytest.mark.parametrize("role,expected", [
    (Role.PROJECT, Color.RED), (Role.PVG, Color.YELLOW), (Role.BVG, Color.RED),
])
def test_role_distractor_color(role, expected):
    assert ticket_color(role, mk("ISReq"), assigned=True, role_distractor=True) == expected


def test_project_completed_isreq_is_red_distractor():
    # A Project engineer's finished ISReq is still off-task → red, overriding the
    # Success-green rule (this is the distractor path build_panel uses).
    done = mk(project="ISReq", status="Done")
    assert ticket_color(Role.PROJECT, done, assigned=True, role_distractor=True) == Color.RED


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
    assert ticket_color(role, t, assigned=assigned) == Color.GREEN


# Role-based reclassification: which assigned tickets become Distractors (#86).
RECLASSIFY_CASES = [
    # BVG: not Highest and not [PR/MP Review] → distractor (ps5 alone is NOT kept).
    (Role.BVG, mk("ISReq"), True),
    (Role.BVG, mk("ISReq", labels=["ps5-blockers"]), True),
    (Role.BVG, mk("ISDB"), True),
    (Role.BVG, mk("ISReq", priority="Highest"), False),
    (Role.BVG, mk("ISReq", title="[PR/MP Review] x"), False),
    # Project: not ISDB → distractor.
    (Role.PROJECT, mk("ISReq"), True),
    (Role.PROJECT, mk("ISDB"), False),
    # PVG: tickets in "In Review" → distractor.
    (Role.PVG, mk("ISReq", status="In Review"), True),
    (Role.PVG, mk("ISReq", status="In Progress"), False),
    # PVG / GEN / OFF: no role-based reclassification.
    (Role.PVG, mk("ISReq"), False),
    (Role.GEN, mk("ISReq"), False),
    (Role.OFF, mk("ISReq"), False),
    # Done is not a distraction for BVG/PVG — but a Project engineer's finished
    # non-ISDB work is still off-task, so it stays a distractor even when Done.
    (Role.BVG, mk("ISReq", status="Done"), False),
    (Role.PVG, mk("ISReq", status="Done"), False),
    (Role.PROJECT, mk("ISReq", status="Done"), True),
    (Role.PROJECT, mk("ISDB", status="Done"), False),
]


@pytest.mark.parametrize("role,ticket,expected", RECLASSIFY_CASES)
def test_is_role_distractor(role, ticket, expected):
    assert is_role_distractor(role, ticket) is expected
