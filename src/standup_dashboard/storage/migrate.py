"""Schema migration entrypoint — ``python -m standup_dashboard.storage.migrate``.

Run once before the app starts (the rock's ``migrate.sh`` invokes this with the
same environment, so it sees ``POSTGRESQL_DB_CONNECT_STRING``). Constructing the
``Database`` runs the idempotent ``CREATE TABLE/INDEX IF NOT EXISTS`` schema;
this is safe to run on every deploy/upgrade. Exits non-zero on failure so the
charm refuses to start an app pointed at an unmigrated database.
"""

from __future__ import annotations

import logging
import sys

from .. import config
from .db import Database

logger = logging.getLogger("standup_dashboard.migrate")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dsn = config.database_dsn()
    logger.info("Applying schema to the configured database")
    db = Database(dsn)
    db.close()
    logger.info("Schema is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
