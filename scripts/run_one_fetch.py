"""Trigger a single refresh cycle (same as the scheduler's tick / the Refresh
button), then report the worklog attribution it produced. One-off helper."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime

from standup_dashboard import config
from standup_dashboard.services import roster
from standup_dashboard.services.fetch import run_fetch
from standup_dashboard.settings import load_secrets
from standup_dashboard.storage.db import Database


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    window_days = int(sys.argv[1]) if len(sys.argv) > 1 else None
    secrets = load_secrets()
    print("tempo token present:", bool(secrets.tempo_token), "| window_days:", window_days)
    db = Database(config.database_dsn())
    roster.load(db)
    fetch_id = await run_fetch(db, secrets, now=datetime.now(UTC), window_days=window_days)
    print("fetch_id:", fetch_id)
    touches = db.get_touches(fetch_id)
    wl = [t for t in touches if t.kind.value == "worklog"]
    print(f"worklog touches: {len(wl)} | hours: {sum(t.seconds for t in wl)/3600:.1f}")
    by_eng: dict[str, int] = defaultdict(int)
    for t in wl:
        by_eng[t.engineer_email] += t.seconds
    for email, secs in sorted(by_eng.items(), key=lambda kv: -kv[1]):
        print(f"  {email:42} {secs/3600:5.1f}h")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
