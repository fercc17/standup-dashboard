"""Role × ticket-kind colour matrix + reclassification (#158, supersedes #86)."""

from __future__ import annotations

import pytest

from standup_dashboard.domain.coloring import (
    alert_classification,
    is_role_distractor,
    ticket_color,
)
from standup_dashboard.domain.models import Color, Role, Ticket, TicketGroup

G, Y, R = Color.GREEN, Color.YELLOW, Color.RED


def test_alert_classification_matrix():
    # PVG own alert duty: resolved green (Success), open ≤24h yellow / >24h red (WIP).
    assert alert_classification(Role.PVG, resolved=True, recent=False) == (G, TicketGroup.SUCCESS)
    assert alert_classification(Role.PVG, resolved=False, recent=True) == (Y, TicketGroup.WIP)
    assert alert_classification(Role.PVG, resolved=False, recent=False) == (R, TicketGroup.WIP)
    # BVG: yellow either way.
    assert alert_classification(Role.BVG, resolved=True, recent=False) == (Y, TicketGroup.SUCCESS)
    assert alert_classification(Role.BVG, resolved=False, recent=True) == (Y, TicketGroup.WIP)
    # GEN / Project / OFF: alerts are a red distraction.
    for role in (Role.GEN, Role.PROJECT, Role.OFF):
        assert alert_classification(role, resolved=True, recent=True) == (R, TicketGroup.DISTRACTORS)


def mk(project="ISReq", status="In Progress", priority=None, labels=None, title="x"):
    return Ticket(
        id=f"{project}-1", project_key=project, title=title, status=status,
        priority=priority, labels=labels or [],
    )


HIGHEST = dict(priority="Highest")
PRMP = dict(title="[PR/MP Review] x")
PS5 = dict(labels=["ps5-blockers"])


# Assigned, in-flight ticket → expected colour, per the final matrix.
ASSIGNED_CASES = [
    # PVG: tickets distract from alert duty — yellow, except regular (red).
    (Role.PVG, mk("ISReq", **HIGHEST), Y), (Role.PVG, mk("ISReq", **PRMP), Y),
    (Role.PVG, mk("ISReq", **PS5), Y), (Role.PVG, mk("ISReq"), R), (Role.PVG, mk("ISDB"), Y),
    # BVG: Highest / PR-MP / ps5 green; regular + ISDB red.
    (Role.BVG, mk("ISReq", **HIGHEST), G), (Role.BVG, mk("ISReq", **PRMP), G),
    (Role.BVG, mk("ISReq", **PS5), G), (Role.BVG, mk("ISReq"), R), (Role.BVG, mk("ISDB"), R),
    # GEN: Highest / ps5 green; PR-MP yellow; regular + ISDB red.
    (Role.GEN, mk("ISReq", **HIGHEST), G), (Role.GEN, mk("ISReq", **PS5), G),
    (Role.GEN, mk("ISReq", **PRMP), Y), (Role.GEN, mk("ISReq"), R), (Role.GEN, mk("ISDB"), R),
    # Project: ISDB green; everything else red.
    (Role.PROJECT, mk("ISDB"), G), (Role.PROJECT, mk("ISReq", **HIGHEST), R),
    (Role.PROJECT, mk("ISReq"), R),
    # OFF: everything red.
    (Role.OFF, mk("ISReq", **HIGHEST), R), (Role.OFF, mk("ISDB"), R),
]


@pytest.mark.parametrize("role,ticket,expected", ASSIGNED_CASES)
def test_assigned_matrix(role, ticket, expected):
    assert ticket_color(role, ticket, assigned=True) == expected


def test_role_distractor_flag_is_ignored():
    # The matrix already encodes the distractor colour, so the flag has no effect.
    t = mk("ISReq", **HIGHEST)
    assert ticket_color(Role.PVG, t, assigned=True, role_distractor=True) == Y
    assert ticket_color(Role.PVG, t, assigned=True, role_distractor=False) == Y


def test_project_completed_isreq_is_red():
    done = mk(project="ISReq", status="Done")
    assert ticket_color(Role.PROJECT, done, assigned=True) == R


NON_ASSIGNED_CASES = [
    (Role.PVG, G), (Role.BVG, G), (Role.GEN, R), (Role.PROJECT, R), (Role.OFF, R),
]


@pytest.mark.parametrize("role,expected", NON_ASSIGNED_CASES)
def test_non_assigned_touch(role, expected):
    assert ticket_color(role, mk("ISReq", **HIGHEST), assigned=False) == expected


@pytest.mark.parametrize("role", [Role.PVG, Role.BVG, Role.GEN, Role.OFF])
@pytest.mark.parametrize("assigned", [True, False])
def test_done_is_green_except_project(role, assigned):
    """Done is green for every role except a Project engineer's non-ISDB work."""
    assert ticket_color(role, mk("ISReq", status="Done"), assigned=assigned) == G


def test_done_isdb_green_for_project():
    assert ticket_color(Role.PROJECT, mk("ISDB", status="Done"), assigned=True) == G


# is_role_distractor: a non-GREEN matrix cell is a distraction (Done exempt,
# except Project non-ISDB).
DISTRACTOR_CASES = [
    (Role.PVG, mk("ISReq", **HIGHEST), True), (Role.PVG, mk("ISReq"), True),
    (Role.PVG, mk("ISDB"), True),
    (Role.BVG, mk("ISReq", **HIGHEST), False), (Role.BVG, mk("ISReq", **PS5), False),
    (Role.BVG, mk("ISReq"), True), (Role.BVG, mk("ISDB"), True),
    (Role.GEN, mk("ISReq", **HIGHEST), False), (Role.GEN, mk("ISReq", **PS5), False),
    (Role.GEN, mk("ISReq", **PRMP), True), (Role.GEN, mk("ISReq"), True),
    (Role.GEN, mk("ISDB"), True),
    (Role.PROJECT, mk("ISReq"), True), (Role.PROJECT, mk("ISDB"), False),
    (Role.OFF, mk("ISReq", **HIGHEST), True),
    # Done exempt — except Project non-ISDB.
    (Role.PVG, mk("ISReq", status="Done"), False),
    (Role.BVG, mk("ISReq", status="Done"), False),
    (Role.GEN, mk("ISReq", status="Done"), False),
    (Role.PROJECT, mk("ISReq", status="Done"), True),
    (Role.PROJECT, mk("ISDB", status="Done"), False),
]


@pytest.mark.parametrize("role,ticket,expected", DISTRACTOR_CASES)
def test_is_role_distractor(role, ticket, expected):
    assert is_role_distractor(role, ticket) is expected
