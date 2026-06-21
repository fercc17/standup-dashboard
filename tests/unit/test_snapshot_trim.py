"""Raw-snapshot changelog trimming (#snapshot-trim).

The bulky avatar-URL / self-link noise in changelog *authors* is ~75% of a Jira
snapshot's bytes and is never read back. Trimming it before storage must stay
lossless for everything the app derives from the changelog (touches, wip_since,
done_date), so a stored snapshot can still be re-processed (FR-028).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from standup_dashboard.services.fetch import (
    _trim_changelog,
    _trim_snapshot_payloads,
)
from standup_dashboard.services.touches import extract_touches, parse_ticket

MEMBER = "alexandre.gomes@canonical.com"  # AMER, on the seed roster


def _author(email: str) -> dict:
    """A full Jira author object — identity plus the avatar/self noise we drop."""
    return {
        "accountId": "acc-123",
        "displayName": "Alexandre Gomes",
        "emailAddress": email,
        "self": "https://warthogs.atlassian.net/rest/api/3/user?accountId=acc-123",
        "active": True,
        "timeZone": "America/Mexico_City",
        "avatarUrls": {
            "48x48": "https://avatar.example/48.png",
            "24x24": "https://avatar.example/24.png",
            "16x16": "https://avatar.example/16.png",
            "32x32": "https://avatar.example/32.png",
        },
    }


def _full_issue() -> dict:
    return {
        "key": "ISReq-9",
        "fields": {
            "summary": "demo",
            "status": {"name": "In Progress",
                       "statusCategory": {"name": "In Progress"}},
            "created": "2026-06-01T12:00:00.000+0000",
            "assignee": {"emailAddress": MEMBER},
            "reporter": {"emailAddress": MEMBER},
        },
        "changelog": {
            "startAt": 0, "maxResults": 2, "total": 2,
            "histories": [
                {
                    "id": "1001",
                    "author": _author(MEMBER),
                    "created": "2026-06-03T12:00:00.000+0000",
                    "items": [{"field": "status", "fieldtype": "jira",
                               "fromString": "To Do", "toString": "In Progress",
                               "from": "10000", "to": "3"}],
                },
                {
                    "id": "1002",
                    "author": _author(MEMBER),
                    "created": "2026-06-04T12:00:00.000+0000",
                    "items": [{"field": "assignee", "fieldtype": "jira",
                               "toString": "Alexandre Gomes", "to": "acc-123"}],
                },
            ],
        },
    }


_WINDOW_START = datetime(2026, 5, 1, tzinfo=UTC)
_WINDOW_END = datetime(2026, 7, 1, tzinfo=UTC)


def _touches(issue: dict):
    return extract_touches(
        issue, window_start=_WINDOW_START, window_end=_WINDOW_END,
        roster_emails={MEMBER},
    )


def test_trim_drops_author_noise_but_keeps_identity():
    trimmed = _trim_changelog(_full_issue())
    author = trimmed["changelog"]["histories"][0]["author"]
    assert author == {"accountId": "acc-123", "displayName": "Alexandre Gomes",
                      "emailAddress": MEMBER}
    assert "avatarUrls" not in author and "self" not in author


def test_trim_is_lossless_for_derived_data():
    full = _full_issue()
    trimmed = _trim_changelog(full)
    # Everything the app re-derives from the changelog is identical.
    assert parse_ticket(trimmed).wip_since == parse_ticket(full).wip_since
    assert _touches(trimmed) == _touches(full)
    assert _touches(trimmed)  # sanity: attribution actually survived the trim


def test_trim_shrinks_the_payload():
    full = _full_issue()
    trimmed = _trim_changelog(full)
    # The trim targets the changelog (avatar/self noise dominates it); the whole
    # issue shrinks too, but the changelog block is where the >50% cut lands.
    full_cl = len(json.dumps(full["changelog"]))
    trimmed_cl = len(json.dumps(trimmed["changelog"]))
    assert trimmed_cl < full_cl / 2
    assert len(json.dumps(trimmed)) < len(json.dumps(full))


def test_trim_does_not_mutate_the_original():
    full = _full_issue()
    before = json.dumps(full)
    _trim_changelog(full)
    assert json.dumps(full) == before


def test_payload_trim_only_touches_jira_lists():
    payloads = {
        "jira_sprint_1.json": [_full_issue()],
        "pagerduty_incidents.json": [{"id": "P1", "status": "resolved"}],
        "oncall.ics": "BEGIN:VCALENDAR\nEND:VCALENDAR",
    }
    out = _trim_snapshot_payloads(payloads)
    # Jira trimmed; PagerDuty + iCal untouched.
    assert "avatarUrls" not in out["jira_sprint_1.json"][0]["changelog"]["histories"][0]["author"]
    assert out["pagerduty_incidents.json"] == payloads["pagerduty_incidents.json"]
    assert out["oncall.ics"] == payloads["oncall.ics"]
