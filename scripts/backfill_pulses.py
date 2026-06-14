"""Backfill the per-pulse history table for Pulses 1-11 of 2026.

The live refresh only collects the *current* active pulse and accumulates the
pulse-history table (``pulse_summary``) going forward, so on a fresh install only
the current pulse exists. This one-time job fills the gap: it reads, strictly
read-only, every ISReq/ISDB ticket created-or-resolved since Mon Jan 5 2026 plus
every team PagerDuty incident in that span, then computes and stores the same
per-region pulse summary the live dashboard does (ISReq new/closed buckets, ISDB
closed, alert ack/resolved) for each pulse — reusing
``services.counts.region_pulse_summary`` so the numbers match live exactly.

Pulse 12 is the live current pulse (already covered); this backfills Pulses 1-11.
A raw JSON snapshot of every payload is written for full-fidelity audit. The job
is idempotent (``upsert_pulse_summary`` is INSERT OR REPLACE), so it is safe to
re-run if rate-limiting cuts a run short.

Run:  uv run python scripts/backfill_pulses.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from standup_dashboard import config
from standup_dashboard.clients import jira as jira_mod
from standup_dashboard.clients import pagerduty as pd_mod
from standup_dashboard.services.counts import region_pulse_summary
from standup_dashboard.services.fetch import _alerts_from_logs
from standup_dashboard.services.offenders import incidents_from_alerts
from standup_dashboard.services.pulse import pulse_window
from standup_dashboard.services.touches import parse_ticket
from standup_dashboard.settings import load_secrets
from standup_dashboard.storage.db import Database
from standup_dashboard.storage.snapshots import SnapshotWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")

# Pulse 1 = Mon Jan 5 2026; Pulse 12 is the live current pulse (not backfilled).
FIRST_PULSE = 1
LAST_PULSE = 11
SINCE_DATE = "2026-01-05"
SINCE_DT = datetime(2026, 1, 5, tzinfo=UTC)

PD_CONCURRENCY = 8
PD_MAX_RETRIES = 6


async def _fetch_tickets(jira: jira_mod.JiraClient) -> tuple[list, list]:
    jql = (
        f"project in ({', '.join(config.PROJECT_KEYS)}) AND "
        f'(created >= "{SINCE_DATE}" OR resolved >= "{SINCE_DATE}")'
    )
    issues = await jira.search(jql, expand_changelog=True)
    logger.info("Jira: %d issues created-or-resolved since %s", len(issues), SINCE_DATE)
    return [parse_ticket(i) for i in issues], issues


async def _log_entries_with_backoff(
    pd: pd_mod.PagerDutyClient, incident_id: str, sem: asyncio.Semaphore
) -> list[dict]:
    """log_entries with bounded concurrency + Retry-After backoff (base client has none)."""
    delay = 1.0
    for attempt in range(PD_MAX_RETRIES):
        async with sem:
            try:
                return await pd.log_entries(incident_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == PD_MAX_RETRIES - 1:
                    raise
                wait = max(float(exc.response.headers.get("Retry-After", delay)), delay)
        await asyncio.sleep(wait)  # released the semaphore before sleeping
        delay = min(delay * 2, 30.0)
    return []


def _build_alerts(incidents: list, all_logs: dict, id_to_email: dict, roster: set[str]) -> list:
    """Turn incidents + their log entries into roster Alert events (reuses fetch.py)."""
    inc_meta = {
        i["id"]: (i.get("title") or i.get("summary"), i.get("html_url"),
                  i.get("incident_number"))
        for i in incidents
    }
    alerts: list = []
    for incident_id, logs in all_logs.items():
        title, url, number = inc_meta.get(incident_id, (None, None, None))
        alerts.extend(
            _alerts_from_logs(incident_id, logs, id_to_email, roster, title, url, number)
        )
    return alerts


async def _fetch_alerts(
    pd: pd_mod.PagerDutyClient, now: datetime, roster: set[str]
) -> tuple[list, list, dict]:
    users = await pd.list_users()
    id_to_email = {u["id"]: u.get("email", "") for u in users}
    incidents = await pd.incidents(SINCE_DT, now, team_ids=config.PAGERDUTY_TEAM_IDS)
    logger.info(
        "PagerDuty: %d incidents since %s; fetching log entries (concurrency %d)...",
        len(incidents), SINCE_DATE, PD_CONCURRENCY,
    )
    sem = asyncio.Semaphore(PD_CONCURRENCY)
    done = 0

    async def _one(inc: dict) -> tuple[str, list[dict]]:
        nonlocal done
        logs = await _log_entries_with_backoff(pd, inc["id"], sem)
        done += 1
        if done % 1000 == 0:
            logger.info("  log entries: %d/%d incidents", done, len(incidents))
        return inc["id"], logs

    all_logs: dict = dict(await asyncio.gather(*(_one(i) for i in incidents)))
    alerts = _build_alerts(incidents, all_logs, id_to_email, roster)
    logger.info("PagerDuty: %d roster alert events from %d incidents", len(alerts), len(incidents))
    return alerts, incidents, all_logs


async def _load_from_snapshot(
    snapshot_dir: Path, secrets, roster: set[str]
) -> tuple[list, list]:
    """Recompute tickets + alerts from a previously-written raw audit snapshot.

    Avoids re-hitting Jira/PagerDuty (only a cheap list_users call is needed for
    the id->email map). Use when a prior run fetched everything but failed later.
    """
    logger.info("Reusing raw snapshot: %s", snapshot_dir)
    issues = json.loads((snapshot_dir / "backfill_jira_search.json").read_text())
    incidents = json.loads((snapshot_dir / "backfill_pagerduty_incidents.json").read_text())
    all_logs = json.loads((snapshot_dir / "backfill_pagerduty_log_entries.json").read_text())
    tickets = [parse_ticket(i) for i in issues]
    async with pd_mod.make_async_client(secrets.pagerduty_token) as phc:
        users = await pd_mod.PagerDutyClient(phc).list_users()
    id_to_email = {u["id"]: u.get("email", "") for u in users}
    alerts = _build_alerts(incidents, all_logs, id_to_email, roster)
    logger.info(
        "Snapshot: %d issues, %d incidents, %d roster alert events",
        len(issues), len(incidents), len(alerts),
    )
    return tickets, alerts


async def main() -> None:
    secrets = load_secrets()
    db = Database("data/dashboard.db")
    db._conn.execute("PRAGMA busy_timeout = 15000")  # tolerate the live app holding a write
    roster = set(config.all_roster_emails())
    now = datetime.now(UTC)

    # Optional: recompute from a saved snapshot dir instead of re-fetching.
    snap_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if snap_arg:
        tickets, alerts = await _load_from_snapshot(Path(snap_arg), secrets, roster)
    else:
        async with jira_mod.make_async_client(secrets.jira_token) as jhc:
            tickets, issues = await _fetch_tickets(jira_mod.JiraClient(jhc))
        async with pd_mod.make_async_client(secrets.pagerduty_token) as phc:
            alerts, incidents, all_logs = await _fetch_alerts(
                pd_mod.PagerDutyClient(phc), now, roster
            )
        raw_path = SnapshotWriter("data/snapshots").write(now, {
            "backfill_jira_search.json": issues,
            "backfill_pagerduty_incidents.json": incidents,
            "backfill_pagerduty_log_entries.json": all_logs,
        })
        logger.info("Raw audit snapshot written: %s", raw_path)

    # Bootstrap the long-lived incident year-history (#146) from every alert in
    # the span, so repeat-offender analysis has data before the live PD floor.
    incidents = incidents_from_alerts(alerts)
    db.upsert_incidents(incidents)
    logger.info("Incident history: upserted %d distinct incidents.", len(incidents))

    print(f"\n{'Pulse':>6}  {'Region':<8} {'new':>5} {'closed':>7} {'isdb_cl':>8} {'alerts':>7}")
    print("  " + "-" * 48)
    for num in range(FIRST_PULSE, LAST_PULSE + 1):
        start, end = pulse_window(num)
        dates = {start + timedelta(days=i) for i in range((end - start).days)}
        for region in config.REGION_KEYS:
            cells = region_pulse_summary(region, tickets, alerts, [], now, dates=dates)
            counts = {m: c.count for m, c in cells.items()}
            breakdowns = {m: c.breakdown for m, c in cells.items()}
            db.upsert_pulse_summary(num, region, counts, breakdowns, now)
            print(f"{num:>6}  {region:<8} {counts['new_total']:>5} "
                  f"{counts['closed_total']:>7} {counts['isdb_closed']:>8} "
                  f"{counts['alerts_total']:>7}")
    logger.info(
        "Backfill complete: pulses %d-%d x %d regions stored.",
        FIRST_PULSE, LAST_PULSE, len(config.REGION_KEYS),
    )


if __name__ == "__main__":
    asyncio.run(main())
