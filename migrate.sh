#!/bin/sh
# Database migration hook for the 12-factor rock. Run once, before the app
# starts, with the same environment as the app (so POSTGRESQL_DB_CONNECT_STRING
# is set). Idempotent (CREATE TABLE/INDEX IF NOT EXISTS); a non-zero exit blocks
# the app from starting against an unmigrated database.
set -eu

# The package lives under src/; put it on the path (the rock copies src/ next to
# this script but doesn't pip-install the project).
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${HERE}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec python -m standup_dashboard.storage.migrate
