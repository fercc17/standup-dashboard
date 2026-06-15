"""Aging-WIP: wip_since derivation, banding, scoping/sort (#147)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.coloring import wip_age_level
from standup_dashboard.domain.models import Color, Ticket
from standup_dashboard.services.aging import build_aging_wip
from standup_dashboard.services.touches import parse_ticket
from tests.fixtures.jira_pd import issue

MEMBER = "alexandre.gomes@canonical.com"   # AMER
OTHER = "barry.price@canonical.com"        # different region


def _j(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _hist(at, frm, to):
    return {"created": at, "items": [{"field": "status", "fromString": frm, "toString": to}]}


def test_wip_since_is_last_entry_into_progress():
    iss = issue("ISReq-1", status="In Progress", created=_j(2026, 6, 1),
                changelog=[_hist(_j(2026, 6, 3), "To Do", "In Progress")])
    assert parse_ticket(iss).wip_since == datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_in_progress_to_in_review_does_not_reset():
    iss = issue("ISReq-2", status="In Review", created=_j(2026, 6, 1), changelog=[
        _hist(_j(2026, 6, 3), "To Do", "In Progress"),
        _hist(_j(2026, 6, 5), "In Progress", "In Review"),
    ])
    assert parse_ticket(iss).wip_since == datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_leaving_and_re_entering_wip_resets_the_clock():
    iss = issue("ISReq-3", status="In Progress", created=_j(2026, 6, 1), changelog=[
        _hist(_j(2026, 6, 2), "To Do", "In Progress"),
        _hist(_j(2026, 6, 3), "In Progress", "To Do"),     # bounced back
        _hist(_j(2026, 6, 6), "To Do", "In Progress"),     # re-entered
    ])
    assert parse_ticket(iss).wip_since == datetime(2026, 6, 6, 12, tzinfo=UTC)


def test_wip_since_none_when_not_in_progress():
    iss = issue("ISReq-4", status="Done", created=_j(2026, 6, 1),
                changelog=[_hist(_j(2026, 6, 3), "In Progress", "Done")])
    assert parse_ticket(iss).wip_since is None


def test_wip_since_is_creation_when_no_status_changes():
    iss = issue("ISReq-5", status="In Progress", created=_j(2026, 6, 1), changelog=[])
    assert parse_ticket(iss).wip_since == datetime(2026, 6, 1, 12, tzinfo=UTC)


def test_wip_age_level_bands():
    day = 86400
    assert wip_age_level(None) is None
    assert wip_age_level(2 * day) is Color.GREEN        # ≤2d
    assert wip_age_level(2 * day + 1) is Color.YELLOW
    assert wip_age_level(5 * day) is Color.YELLOW        # ≤5d
    assert wip_age_level(5 * day + 1) is Color.RED


def test_build_aging_wip_scopes_to_members_and_sorts_oldest_first():
    now = datetime(2026, 6, 14, 12, tzinfo=UTC)

    def tk(key, assignee, status, days):
        return Ticket(id=key, project_key="ISReq", title=f"{key} t", status=status,
                      priority=None, assignee_email=assignee,
                      wip_since=now - timedelta(days=days) if days is not None else None)

    tickets = [
        tk("A", MEMBER, "In Progress", 6),   # red, member, oldest
        tk("B", MEMBER, "In Review", 1),     # green, member, WIP
        tk("C", OTHER, "In Progress", 10),   # excluded — not a selected member
        tk("D", MEMBER, "Done", None),       # excluded — not WIP
        tk("E", MEMBER, "To Do", None),      # excluded — not WIP
    ]
    rows = build_aging_wip(tickets, {MEMBER}, now)
    assert [r.key for r in rows] == ["A", "B"]            # member WIP only, oldest first
    assert rows[0].level is Color.RED and rows[1].level is Color.GREEN
    assert rows[0].url.endswith("/browse/A")


def test_build_aging_wip_excludes_blocked():
    now = datetime(2026, 6, 14, 12, tzinfo=UTC)

    def tk(key, status, category, days):
        return Ticket(id=key, project_key="ISReq", title=f"{key} t", status=status,
                      status_category=category, priority=None, assignee_email=MEMBER,
                      wip_since=now - timedelta(days=days))

    tickets = [
        tk("A", "In Progress", "In Progress", 6),  # kept
        tk("B", "Blocked", "In Progress", 90),      # excluded despite WIP category
        tk("C", "blocked", "In Progress", 30),      # excluded, case-insensitive
    ]
    rows = build_aging_wip(tickets, {MEMBER}, now)
    assert [r.key for r in rows] == ["A"]
