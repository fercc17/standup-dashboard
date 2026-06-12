"""PagerDuty incidents window never starts before the hard floor (June 08)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from standup_dashboard.services.fetch import _fetch_pagerduty
from standup_dashboard.settings import Secrets

SECRETS = Secrets(jira_token="j", pagerduty_token="p", pagerduty_ical_url="u")


@respx.mock
async def test_since_is_floored_to_june_08():
    respx.get("https://api.pagerduty.com/users").mock(
        return_value=httpx.Response(200, json={"users": [], "more": False})
    )
    incidents = respx.get("https://api.pagerduty.com/incidents").mock(
        return_value=httpx.Response(200, json={"incidents": [], "more": False})
    )

    now = datetime(2026, 7, 1, tzinfo=UTC)
    since = now - timedelta(days=180)  # far before the floor
    await _fetch_pagerduty(SECRETS, now, since, set())

    assert incidents.called
    sent_since = incidents.calls[0].request.url.params["since"]
    assert sent_since.startswith("2026-06-08"), sent_since
