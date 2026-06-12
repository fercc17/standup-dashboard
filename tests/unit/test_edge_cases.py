"""Edge-case unit tests (T056): no pulse, missing creds, zero activity, BVG review."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from standup_dashboard.domain.models import Ticket, TicketGroup, TouchEvent, TouchKind
from standup_dashboard.services.classification import classify_for_engineer
from standup_dashboard.services.counts import build_counts
from standup_dashboard.settings import SetupError, load_secrets

EMAIL = "eng@example.com"


def utc(h=12):
    return datetime(2026, 6, 11, h, tzinfo=UTC)


def test_no_active_pulse_yields_no_counts_rows():
    rows = build_counts(["AMER"], tickets=[], alerts=[], pulses=[], now=utc())
    assert rows == []


def test_missing_credential_file_raises_setup_error(tmp_path):
    with pytest.raises(SetupError) as exc:
        load_secrets(tmp_path)  # empty dir → first file missing
    assert exc.value.missing_file == "secrets/jira_token.txt"


def test_empty_credential_file_raises_setup_error(tmp_path):
    (tmp_path / "jira_token.txt").write_text("   ", encoding="utf-8")
    with pytest.raises(SetupError) as exc:
        load_secrets(tmp_path)
    assert exc.value.missing_file == "secrets/jira_token.txt"


def test_engineer_with_zero_activity_classifies_empty():
    groups = classify_for_engineer(EMAIL, tickets=[], touches=[], pulse_sprint_ids={})
    assert all(len(v) == 0 for v in groups.values())


def test_touched_ticket_with_no_pulse_is_a_distractor():
    t = Ticket(id="ISReq-1", project_key="ISReq", title="x", status="In Progress",
               priority=None, labels=[], assignee_email=EMAIL, sprint_id=999)
    touches = [TouchEvent("ISReq-1", EMAIL, TouchKind.COMMENT, utc())]
    groups = classify_for_engineer(EMAIL, [t], touches, pulse_sprint_ids={})  # no active sprint
    assert groups[TicketGroup.DISTRACTORS] == [t]
    assert groups[TicketGroup.WIP] == []


def test_assigned_unsprinted_untriaged_is_todo_not_distractor():
    # Assigned to E, Untriaged (To Do), but in no sprint (sprint_id=None) — it is
    # the engineer's queued work, so it belongs in To Do, not Distractors.
    t = Ticket(id="ISREQ-2556", project_key="ISReq", title="x", status="Untriaged",
               priority="Medium", labels=[], assignee_email=EMAIL, sprint_id=None,
               status_category="To Do")
    groups = classify_for_engineer(EMAIL, [t], [], pulse_sprint_ids={"ISReq": 34046})
    assert groups[TicketGroup.TODO] == [t]
    assert groups[TicketGroup.DISTRACTORS] == []


def test_touched_unassigned_done_in_pulse_is_a_success():
    # Someone else's ticket, Done and in the active pulse, that E only touched →
    # counts as Success, not a Distractor (#74).
    t = Ticket(id="ISDB-7", project_key="ISDB", title="x", status="Done",
               priority=None, labels=[], assignee_email="other@example.com", sprint_id=101)
    touches = [TouchEvent("ISDB-7", EMAIL, TouchKind.STATUS, utc())]
    groups = classify_for_engineer(EMAIL, [t], touches, pulse_sprint_ids={"ISDB": 101})
    assert groups[TicketGroup.SUCCESS] == [t]
    assert groups[TicketGroup.DISTRACTORS] == []


def test_touched_unassigned_done_outside_pulse_stays_a_distractor():
    t = Ticket(id="ISDB-8", project_key="ISDB", title="x", status="Done",
               priority=None, labels=[], assignee_email="other@example.com", sprint_id=999)
    touches = [TouchEvent("ISDB-8", EMAIL, TouchKind.STATUS, utc())]
    groups = classify_for_engineer(EMAIL, [t], touches, pulse_sprint_ids={"ISDB": 101})
    assert groups[TicketGroup.DISTRACTORS] == [t]
    assert groups[TicketGroup.SUCCESS] == []


def test_pr_mp_review_prefix_detection():
    review = Ticket(id="ISReq-5", project_key="ISReq", title="[PR/MP Review] fix things",
                    status="In Progress", priority=None, labels=[])
    assert review.is_bvg_review is True

    # Same title on ISDB is not a BVG review type.
    isdb = Ticket(id="ISDB-5", project_key="ISDB", title="[PR/MP Review] fix things",
                  status="In Progress", priority=None, labels=[])
    assert isdb.is_bvg_review is False

    # ISReq without the prefix is not a review type.
    plain = Ticket(id="ISReq-6", project_key="ISReq", title="normal work",
                   status="In Progress", priority=None, labels=[])
    assert plain.is_bvg_review is False
