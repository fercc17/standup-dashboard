"""Attribution survives Atlassian hiding ``emailAddress`` (#priv-email).

Accounts with a private email-visibility profile (e.g. Colin Misare, Loïc Gomez)
come back from Jira with an ``accountId`` but no ``emailAddress``. Tickets and
touches must still be attributed to them, via an accountId→email fallback map.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from standup_dashboard.clients.jira import JiraClient
from standup_dashboard.domain.models import TouchKind
from standup_dashboard.services.touches import (
    extract_touches,
    parse_ticket,
    seed_account_emails,
)

COLIN = "colin.misare@canonical.com"
ACCT = "5f4e67fe347294003eaa8766"

# A private-email actor: accountId present, no emailAddress (what Jira returns).
_PRIVATE = {"accountId": ACCT, "displayName": "Colin Misare", "active": True}


def _private_issue(key: str, **fields):
    base = {"summary": f"{key} summary", "status": {"name": "In Progress"},
            "assignee": dict(_PRIVATE), "reporter": dict(_PRIVATE)}
    base.update(fields)
    return {"key": key, "fields": base, "changelog": {"histories": []}}


def test_parse_ticket_resolves_private_assignee_via_account_map():
    iss = _private_issue("ISReq-2667")
    amap = {ACCT: COLIN}
    t = parse_ticket(iss, amap)
    assert t.assignee_email == COLIN
    assert t.reporter_email == COLIN


def test_parse_ticket_without_map_drops_private_assignee():
    # Reproduces the original bug: no map → no email → unattributable.
    t = parse_ticket(_private_issue("ISReq-2667"))
    assert t.assignee_email is None


def test_parse_ticket_prefers_visible_email_over_map():
    iss = _private_issue("ISReq-1")
    iss["fields"]["assignee"] = {"accountId": ACCT, "emailAddress": COLIN}
    # A stale/wrong map entry must never override an email Jira actually exposed.
    t = parse_ticket(iss, {ACCT: "someone.else@canonical.com"})
    assert t.assignee_email == COLIN


def test_seed_account_emails_builds_reverse_map_and_skips_private():
    visible = {"key": "ISReq-1", "fields": {
        "assignee": {"accountId": "acc-a", "emailAddress": "a@x.com"},
        "reporter": {"accountId": "acc-b", "emailAddress": "b@x.com"}},
        "changelog": {"histories": [
            {"author": {"accountId": "acc-c", "emailAddress": "c@x.com"}, "items": []}]}}
    private = _private_issue("ISReq-2")  # accountId only, no email
    amap = seed_account_emails([visible, private])
    assert amap == {"acc-a": "a@x.com", "acc-b": "b@x.com", "acc-c": "c@x.com"}
    assert ACCT not in amap  # the private actor contributes nothing


def test_extract_touches_resolves_private_author_and_worklog():
    at = "2026-06-12T12:00:00.000+0000"
    iss = _private_issue("ISReq-2667")
    iss["changelog"] = {"histories": [
        {"author": dict(_PRIVATE), "created": at, "items": [{"field": "status"}]}]}
    # Tempo bot worklog → attributed to the (private) assignee proxy.
    worklogs = [{"author": {"displayName": "Tempo"}, "started": at,
                 "timeSpentSeconds": 3600}]
    touches = extract_touches(
        iss, worklogs=worklogs,
        window_start=datetime(2026, 6, 8, tzinfo=UTC),
        window_end=datetime(2026, 6, 15, tzinfo=UTC),
        roster_emails={COLIN}, account_emails={ACCT: COLIN})
    kinds = {t.kind for t in touches}
    assert TouchKind.STATUS in kinds and TouchKind.WORKLOG in kinds
    assert {t.engineer_email for t in touches} == {COLIN}
    assert sum(t.seconds for t in touches if t.kind is TouchKind.WORKLOG) == 3600


@respx.mock
async def test_account_ids_for_recovers_private_and_skips_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        if q == COLIN:  # private: real accountId, blank email
            return httpx.Response(200, json=[{"accountId": ACCT, "emailAddress": ""}])
        if q == "chris@x.com":  # public: exact email match preferred over a decoy
            return httpx.Response(200, json=[
                {"accountId": "decoy", "emailAddress": "chrisother@x.com"},
                {"accountId": "acc-chris", "emailAddress": "chris@x.com"}])
        if q == "gone@x.com":  # no hits → skipped
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"errorMessages": ["nope"]})  # error → skipped

    respx.get(url__regex=r"https://warthogs\.atlassian\.net/rest/api/3/user/search.*").mock(
        side_effect=handler)
    async with httpx.AsyncClient(base_url="https://warthogs.atlassian.net") as hc:
        amap = await JiraClient(hc).account_ids_for(
            [COLIN, "chris@x.com", "gone@x.com", "boom@x.com"])
    assert amap == {ACCT: COLIN, "acc-chris": "chris@x.com"}
