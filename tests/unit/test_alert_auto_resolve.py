"""Auto-resolved & re-queried alerts move ACK→RESOLVED (#stale-ack).

A PagerDuty incident resolved by the alerting integration (no human agent) — the
common Prometheus "FIRING" case — must still leave the acker's ACK bucket. And an
incident created before the incremental window must be re-queried so a later
resolve is caught.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from standup_dashboard.domain.models import AlertState
from standup_dashboard.services.fetch import _alerts_from_logs, _fetch_pagerduty
from standup_dashboard.settings import Secrets

ACKER = "afif.refrizal@canonical.com"
ID_TO_EMAIL = {"PU1": ACKER}
ROSTER = {ACKER}

ACK = {"type": "acknowledge_log_entry", "agent": {"id": "PU1"},
       "created_at": "2026-06-10T16:52:32Z"}
# Auto-resolve by the alerting integration: agent is not a roster user.
INTEGRATION_RESOLVE = {
    "type": "resolve_log_entry",
    "agent": {"id": "PINTEG", "type": "events_api_v2_inbound_integration_reference"},
    "created_at": "2026-06-10T17:12:19Z",
}


def _by_state(alerts):
    return {a.state: a for a in alerts}


def test_integration_resolve_attributed_to_acker():
    alerts = _alerts_from_logs("INC1", [ACK, INTEGRATION_RESOLVE], ID_TO_EMAIL, ROSTER)
    by_state = _by_state(alerts)
    assert AlertState.ACKNOWLEDGED in by_state
    resolved = by_state.get(AlertState.RESOLVED)
    assert resolved is not None, "auto-resolve must produce a RESOLVED alert"
    assert resolved.handler_email == ACKER       # attributed to the acker
    assert resolved.at == datetime(2026, 6, 10, 17, 12, 19, tzinfo=UTC)


def test_human_resolve_credits_resolver_not_duplicated():
    human_resolve = {"type": "resolve_log_entry", "agent": {"id": "PU1"},
                     "created_at": "2026-06-10T17:00:00Z"}
    alerts = _alerts_from_logs("INC1", [ACK, human_resolve], ID_TO_EMAIL, ROSTER)
    resolved = [a for a in alerts if a.state is AlertState.RESOLVED]
    assert len(resolved) == 1 and resolved[0].handler_email == ACKER


def test_open_ack_with_no_resolve_stays_acknowledged():
    alerts = _alerts_from_logs("INC1", [ACK], ID_TO_EMAIL, ROSTER)
    assert all(a.state is not AlertState.RESOLVED for a in alerts)


@respx.mock
async def test_fetch_pagerduty_rechecks_out_of_window_incident():
    # Window returns nothing (incident created earlier in the pulse), but it's in
    # recheck_ids → its logs are fetched and the integration auto-resolve surfaces.
    respx.get("https://api.pagerduty.com/users").mock(
        return_value=httpx.Response(200, json={
            "users": [{"id": "PU1", "email": ACKER}], "more": False})
    )
    respx.get("https://api.pagerduty.com/incidents").mock(
        return_value=httpx.Response(200, json={"incidents": [], "more": False})
    )
    logs = respx.get(url__regex=r"https://api\.pagerduty\.com/incidents/INC-OLD/log_entries.*").mock(
        return_value=httpx.Response(200, json={
            "log_entries": [ACK, INTEGRATION_RESOLVE], "more": False})
    )

    secrets = Secrets(jira_token="j", pagerduty_token="p", pagerduty_ical_url="u")
    now = datetime(2026, 6, 16, tzinfo=UTC)
    res = await _fetch_pagerduty(secrets, now, now, ROSTER, frozenset({"INC-OLD"}))

    assert logs.called  # the out-of-window incident was re-queried
    resolved = [a for a in res.alerts if a.state is AlertState.RESOLVED]
    assert resolved and resolved[0].handler_email == ACKER
