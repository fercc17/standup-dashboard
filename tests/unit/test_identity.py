"""Roster ↔ PagerDuty identity gate (FR-005a / T021)."""

from __future__ import annotations

import httpx
import pytest
import respx

from standup_dashboard import config
from standup_dashboard.services.identity import validate_identities
from standup_dashboard.settings import Secrets, SetupError

SECRETS = Secrets(jira_token="j", pagerduty_token="p", jira_ical_url="http://x/o.ics")


def _users(emails):
    return [{"id": f"PU{i}", "email": e, "name": e} for i, e in enumerate(emails)]


@respx.mock
def test_all_roster_matched_passes():
    respx.get("https://api.pagerduty.com/users").mock(
        return_value=httpx.Response(200, json={"users": _users(config.all_roster_emails()),
                                               "more": False})
    )
    validate_identities(SECRETS)  # no raise


@respx.mock
def test_unmatched_engineer_raises_setup_error():
    missing = config.all_roster_emails()[0]
    present = config.all_roster_emails()[1:]
    respx.get("https://api.pagerduty.com/users").mock(
        return_value=httpx.Response(200, json={"users": _users(present), "more": False})
    )
    with pytest.raises(SetupError) as exc:
        validate_identities(SECRETS)
    assert missing in exc.value.unmatched_engineers
