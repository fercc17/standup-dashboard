"""Roster ↔ PagerDuty identity gate (FR-005a) — T021.

Every roster engineer email MUST resolve to a PagerDuty user. Any unmatched
engineer is a blocking setup error naming them; the web layer renders the
setup page instead of the dashboard.
"""

from __future__ import annotations

import asyncio
import concurrent.futures

import httpx

from .. import config
from ..clients.pagerduty import PagerDutyClient, make_async_client
from ..settings import Secrets, SetupError


async def _unmatched_emails(token: str) -> list[str]:
    async with make_async_client(token) as hc:
        users = await PagerDutyClient(hc).list_users()
    known = {(u.get("email") or "").lower() for u in users}
    # Only the curated seed roster is a hard gate; UI-added engineers (#16) are
    # best-effort and must not block startup.
    return [e for e in config.seed_roster_emails() if e.lower() not in known]


def _run_sync(coro):
    """Run a coroutine to completion from sync code, whether or not an event loop
    is already running. ``create_app`` validates identities synchronously; under
    ``--reload`` uvicorn loads the app *inside* its loop, where ``asyncio.run``
    would raise — so fall back to a one-shot worker thread in that case."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def validate_identities(secrets: Secrets) -> None:
    """Raise SetupError if any roster email has no PagerDuty match (FR-005a).

    A PagerDuty request failure (bad token, network/outage) is also surfaced as
    a blocking setup page rather than crashing startup.
    """
    try:
        unmatched = _run_sync(_unmatched_emails(secrets.pagerduty_token))
    except httpx.HTTPError as exc:
        raise SetupError(
            "Could not validate engineer identities against PagerDuty — check that "
            f"secrets/pagerduty_token.txt holds a valid token and PagerDuty is reachable. ({exc})"
        ) from exc
    if unmatched:
        names = ", ".join(unmatched)
        raise SetupError(
            f"These roster engineers have no matching PagerDuty identity: {names}.",
            unmatched_engineers=unmatched,
        )
