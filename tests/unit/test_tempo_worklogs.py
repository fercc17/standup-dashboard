"""Tempo worklog attribution (#tempo-worklogs).

With a Tempo token, worklog time is credited to the *real logger* (the Tempo
``author.accountId`` resolved to a roster email) instead of the ticket assignee,
so time SRE B logs on SRE A's ticket lands on B's card — and the ticket shows on
both. Also covers the optional-secret gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import TouchKind
from standup_dashboard.services.touches import _tempo_started, tempo_worklog_touches
from standup_dashboard.settings import load_secrets

BEN = "benjamin.allot@canonical.com"   # ISREQ-3052 assignee
PAUL = "paul.collins@canonical.com"    # logs time on Ben's ticket
OUTSIDER = "someone.else@example.com"  # not on the roster

ID_TO_KEY = {"100123": "ISREQ-3052"}
ROSTER = {BEN, PAUL}
ACCOUNTS = {"acc-paul": PAUL, "acc-ben": BEN}
WIN_START = datetime(2026, 6, 13, tzinfo=UTC)
WIN_END = datetime(2026, 6, 20, tzinfo=UTC)


def _wl(account_id, *, issue_id="100123", seconds=3600,
        start_date="2026-06-18", start_time="09:00:00"):
    return {
        "issue": {"id": issue_id},
        "author": {"accountId": account_id},
        "timeSpentSeconds": seconds,
        "startDate": start_date,
        "startTime": start_time,
        "startDateTimeUtc": f"{start_date}T{start_time}Z",
    }


def _touches(worklogs):
    return tempo_worklog_touches(
        worklogs, id_to_key=ID_TO_KEY, window_start=WIN_START, window_end=WIN_END,
        roster_emails=ROSTER, account_emails=ACCOUNTS,
    )


def test_credits_the_real_logger_not_the_assignee():
    # Paul logs on Ben's ticket → the touch is Paul's, on Ben's ticket.
    [t] = _touches([_wl("acc-paul")])
    assert t.engineer_email == PAUL
    assert t.ticket_id == "ISREQ-3052"
    assert t.kind is TouchKind.WORKLOG
    assert t.seconds == 3600
    assert t.at == datetime(2026, 6, 18, 9, 0, tzinfo=UTC)


def test_worklog_on_unfetched_issue_is_dropped():
    assert _touches([_wl("acc-paul", issue_id="999999")]) == []


def test_non_roster_author_is_dropped():
    assert _touches([_wl("acc-unknown")]) == []          # accountId not in ACCOUNTS
    extra = {**ACCOUNTS, "acc-out": OUTSIDER}
    assert tempo_worklog_touches(
        [_wl("acc-out")], id_to_key=ID_TO_KEY, window_start=WIN_START,
        window_end=WIN_END, roster_emails=ROSTER, account_emails=extra) == []


def test_out_of_window_worklog_is_dropped():
    assert _touches([_wl("acc-paul", start_date="2026-06-01")]) == []


def test_backdated_worklog_created_in_window_is_kept():
    # The work-time (started) predates the incremental window, but the entry was
    # *created* within it — Tempo lets you log late and backdate. It must be kept,
    # stamped at its started time, so it isn't dropped forever (#tempo-backdate).
    w = _wl("acc-paul", start_date="2026-06-10")     # started before WIN_START (06-13)
    w["createdAt"] = "2026-06-18T03:10:00Z"          # but logged in-window
    [t] = _touches([w])
    assert t.engineer_email == PAUL
    assert t.at == datetime(2026, 6, 10, 9, 0, tzinfo=UTC)   # bucketed at the work-time


def test_backdated_worklog_created_out_of_window_still_dropped():
    # Backdated work AND a create time before the window → genuinely old, dropped.
    w = _wl("acc-paul", start_date="2026-06-10")
    w["createdAt"] = "2026-06-01T00:00:00Z"
    assert _touches([w]) == []


def test_duplicate_worklogs_are_deduped():
    assert len(_touches([_wl("acc-paul"), _wl("acc-paul")])) == 1


def test_prefers_utc_instant_over_local_start_fields():
    # Tempo's local startDate/startTime (08:00 for an APAC logger) differ from the
    # true UTC instant (the prior evening); both fall in-window here, so the touch
    # survives and must be bucketed by the UTC instant, not the local day.
    w = {"issue": {"id": "100123"}, "author": {"accountId": "acc-paul"},
         "timeSpentSeconds": 3600, "startDate": "2026-06-16", "startTime": "08:00:00",
         "startDateTimeUtc": "2026-06-15T22:00:00Z"}
    [t] = _touches([w])
    assert t.at == datetime(2026, 6, 15, 22, 0, tzinfo=UTC)


def test_tempo_started_prefers_utc_then_falls_back():
    assert _tempo_started({"startDateTimeUtc": "2026-06-12T22:00:00Z"}) == \
        datetime(2026, 6, 12, 22, tzinfo=UTC)
    assert _tempo_started({"startDate": "2026-06-18"}) == datetime(2026, 6, 18, tzinfo=UTC)
    assert _tempo_started({}) is None


def test_tempo_token_is_optional(tmp_path):
    for f in ("jira_token.txt", "pagerduty_token.txt", "pagerduty_ical_url.txt"):
        (tmp_path / f).write_text("x", encoding="utf-8")
    assert load_secrets(tmp_path).tempo_token is None
    (tmp_path / "tempo_token.txt").write_text("tmpo_abc", encoding="utf-8")
    assert load_secrets(tmp_path).tempo_token == "tmpo_abc"
