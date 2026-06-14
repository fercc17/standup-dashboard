"""Backfill ``ticket.wip_since`` for rows stored before the aging-WIP feature (#147).

The live fetch only returns the active sprint + recently created/resolved issues,
so an old in-progress ticket (e.g. months In Review, not in the current sprint)
is carried forward in the merged view from a *pre-feature* fetch where
``wip_since`` was never computed — it renders with a "—" age.

This re-parses the **raw Jira snapshots already on disk** (which carry the
changelog) and fills ``wip_since`` for any ticket row still missing it. Strictly
local + read-only toward Jira (no API calls); idempotent. Newer fetches already
populate ``wip_since``, so this is a one-time catch-up.

Run:  uv run python scripts/backfill_wip_since.py
"""

from __future__ import annotations

import glob
import json
import logging
import os

from standup_dashboard.services.touches import parse_ticket
from standup_dashboard.storage.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill-wip")


def _issues(path: str) -> list[dict]:
    try:
        data = json.load(open(path))
    except Exception:  # noqa: BLE001 — skip unreadable/empty snapshot files
        return []
    return data if isinstance(data, list) else data.get("issues", [])


def main() -> None:
    db = Database("data/dashboard.db")
    db._conn.execute("PRAGMA busy_timeout = 15000")

    # Newest snapshot first → the first computed value per ticket is the freshest.
    files = sorted(glob.glob("data/snapshots/*/jira_*.json"), reverse=True)
    wip_since: dict[str, str] = {}
    for path in files:
        for issue in _issues(path):
            key = issue.get("key")
            if not key or key in wip_since:
                continue
            t = parse_ticket(issue)
            if t.wip_since is not None:
                wip_since[key] = t.wip_since.isoformat()
    logger.info("Computed wip_since for %d tickets from %d snapshot files.",
                len(wip_since), len(files))

    filled = 0
    for key, ts in wip_since.items():
        cur = db._conn.execute(
            "UPDATE ticket SET wip_since = ? WHERE id = ? AND wip_since IS NULL",
            (ts, key),
        )
        filled += cur.rowcount
    db._conn.commit()
    logger.info("Backfilled wip_since on %d ticket rows.", filled)


if __name__ == "__main__":
    main()
