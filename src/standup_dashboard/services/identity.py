"""Roster ↔ PagerDuty identity gate (FR-005a) — T021.

Every roster engineer email MUST resolve to a PagerDuty user. Any unmatched
engineer is a blocking setup error naming them; the web layer renders the
setup page instead of the dashboard.
"""

from __future__ import annotations

import asyncio

from .. import config
from ..clients.pagerduty import PagerDutyClient, make_async_client
from ..settings import Secrets, SetupError


async def _unmatched_emails(token: str) -> list[str]:
    async with make_async_client(token) as hc:
        users = await PagerDutyClient(hc).list_users()
    known = {(u.get("email") or "").lower() for u in users}
    return [e for e in config.all_roster_emails() if e.lower() not in known]


def validate_identities(secrets: Secrets) -> None:
    """Raise SetupError if any roster email has no PagerDuty match (FR-005a)."""
    unmatched = asyncio.run(_unmatched_emails(secrets.pagerduty_token))
    if unmatched:
        names = ", ".join(unmatched)
        raise SetupError(
            f"These roster engineers have no matching PagerDuty identity: {names}.",
            unmatched_engineers=unmatched,
        )
