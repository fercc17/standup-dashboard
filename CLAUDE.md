<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan at
`specs/001-sre-standup-dashboard/plan.md` (with design detail in the sibling
`research.md`, `data-model.md`, `contracts/`, and `quickstart.md`).

Stack: Python 3.12 · FastAPI + uvicorn · Jinja2 + HTMX (no build) · httpx ·
PostgreSQL via psycopg (raw payloads as JSONB) · icalendar · zoneinfo · uv ·
pytest (ephemeral Postgres via pytest-postgresql). Strictly read-only toward
Jira & PagerDuty. Secrets come from env first (the charm injects them), falling
back to gitignored `secrets/*.txt` for local dev.

Deployed as a Canonical Kubernetes 12-factor FastAPI charm — see `CHARM.md` for
build/deploy, `rockcraft.yaml` / `charmcraft.yaml`, and `migrate.sh`. The DB DSN
comes from `POSTGRESQL_DB_CONNECT_STRING` (charm) or `STANDUP_DB_DSN` (local);
the rock runs the web app plus a singleton `refresh-scheduler` service.
<!-- SPECKIT END -->
