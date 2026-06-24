"""ISDB estimate vs invested effort badge on ticket lines (#isdb-estimate)."""

from __future__ import annotations

from standup_dashboard.domain.models import Ticket
from standup_dashboard.services.touches import parse_ticket
from standup_dashboard.web.presenters import _effort_badge
from tests.fixtures.jira_pd import issue


def _t(project: str = "ISDB", est: int | None = None, spent: int | None = None) -> Ticket:
    return Ticket("ISDB-1", project, "x", "In Progress", None,
                  estimate_seconds=est, spent_seconds=spent)


def test_parse_ticket_reads_time_tracking():
    t = parse_ticket(issue("ISDB-1", estimate_seconds=3600, spent_seconds=21600))
    assert (t.estimate_seconds, t.spent_seconds) == (3600, 21600)


def test_parse_ticket_time_tracking_absent_is_none():
    t = parse_ticket(issue("ISDB-2"))
    assert t.estimate_seconds is None and t.spent_seconds is None


def test_effort_badge_estimate_and_invested_flags_overrun():
    label, title, over = _effort_badge(_t(est=3600, spent=21600))  # 1h est, 6h spent
    assert label == "1h ▸ 6h · 600%"
    assert "6h invested of 1h estimate (600%)" in title
    assert over is True


def test_effort_badge_under_estimate_shows_percent():
    label, _title, over = _effort_badge(_t(est=28800, spent=14400))  # 8h est, 4h spent
    assert (label, over) == ("8h ▸ 4h · 50%", False)


def test_effort_badge_estimate_without_invested_is_zero_percent():
    label, _title, over = _effort_badge(_t(est=3600, spent=None))
    assert (label, over) == ("1h ▸ 0m · 0%", False)


def test_effort_badge_invested_without_estimate():
    label, title, over = _effort_badge(_t(est=None, spent=43800))  # 12h 10m, no estimate
    assert label == "▸ 12h 10m"
    assert over is False
    assert "no estimate" in title


def test_effort_badge_blank_for_non_isdb():
    assert _effort_badge(_t(project="ISReq", est=3600, spent=21600)) == ("", "", False)


def test_effort_badge_blank_when_no_time_data():
    assert _effort_badge(_t()) == ("", "", False)
