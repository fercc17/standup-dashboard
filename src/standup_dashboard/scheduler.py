"""Periodic refresh worker — ``python -m standup_dashboard.scheduler``.

Runs as the charm's ``-scheduler`` rock service, which Juju places on exactly one
unit, so it is the single writer that keeps the dashboard's data fresh: every
``REFRESH_INTERVAL_SECONDS`` it calls the same ``run_fetch`` the manual refresh
button uses. Database DSN, secrets and config all come from the environment
(Postgres relation + Juju secret), identical to the web app — no shared state on
disk. A failed cycle is logged and retried on the next tick; it never exits the
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from . import config
from .services.fetch import run_fetch
from .settings import Secrets, SetupError, load_secrets
from .storage.db import Database

logger = logging.getLogger("standup_dashboard.scheduler")


async def _run_once(
    db: Database, secrets: Secrets, sources: frozenset[str], now: datetime
) -> None:
    try:
        fetch_id = await run_fetch(db, secrets, sources=sources, now=now)
        logger.info("Scheduled refresh of %s complete (fetch_id=%s)", sorted(sources), fetch_id)
    except Exception:  # noqa: BLE001 — never let one bad cycle kill the loop
        logger.exception("Scheduled refresh of %s failed; will retry on its next slot",
                         sorted(sources))


async def main_async() -> None:
    try:
        secrets = load_secrets()
    except SetupError as exc:
        logger.error("Scheduler cannot start: %s", exc.message)
        raise SystemExit(1) from exc

    db = Database(config.database_dsn())
    # Apply saved roster overrides so attribution matches the web app (#16).
    from .services import roster
    roster.load(db)

    logger.info("Per-source refresh scheduler started: %s; iCal daily @ %02d:00 UTC",
                {s: sorted(m) for s, m in config.SOURCE_SCHEDULE_MINUTES.items()},
                config.ICAL_DAILY_HOUR)
    # Cold start: one full fetch so the dashboard has data immediately, then follow the
    # per-source cron — wake each minute and refresh whatever's due (#per-source-schedule).
    await _run_once(db, secrets, config.ALL_SOURCES, datetime.now(UTC))
    last_key = (lambda n: (n.hour, n.minute))(datetime.now(UTC))
    try:
        while True:
            await asyncio.sleep(max(1, 60 - datetime.now(UTC).second))
            now = datetime.now(UTC)
            key = (now.hour, now.minute)
            if key == last_key:           # don't run the same minute twice
                continue
            last_key = key
            due = config.due_sources(now)
            if due:
                await _run_once(db, secrets, due, now)
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    main()
