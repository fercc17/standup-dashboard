# Deploying the dashboard as a Kubernetes charm

The app is packaged as a Canonical **12-factor FastAPI charm**: the code is built
into a *rock* (OCI image) with `rockcraft`, wrapped by a *charm* with
`charmcraft`, and deployed on **Canonical Kubernetes** with Juju. State lives in
PostgreSQL (relation); raw fetch payloads are JSONB rows; a singleton
`-scheduler` service refreshes the data; Traefik provides ingress.

## What changed from the local app

- **Storage is PostgreSQL** (`src/standup_dashboard/storage/db.py`), not SQLite.
  Connection string comes from `POSTGRESQL_DB_CONNECT_STRING` (charm relation) or
  `STANDUP_DB_DSN` locally (`config.database_dsn()`).
- **Raw snapshots → JSONB** in the `raw_snapshot` table (no more `data/snapshots/`).
- **Secrets from env first**, file fallback (`settings.py`): the charm injects a
  Juju secret; locally `secrets/*.txt` still work.
- **Config from env**: `config.py` reads `APP_*` (charm) / `STANDUP_*` (local).
- **Module-level ASGI app** at project-root `app.py` (the rock can't call a factory).
- **Scheduler**: `python -m standup_dashboard.scheduler` runs `run_fetch` on an
  interval; it's the rock's `refresh-scheduler` service (one unit).
- **Migrations**: `migrate.sh` → `python -m standup_dashboard.storage.migrate`
  (idempotent schema), run by the rock before the app starts.

## Local development (PostgreSQL)

Tests and local runs need a PostgreSQL server binary (`pg_ctl`/`initdb`) on PATH.

```sh
# Run the suite (ephemeral Postgres per test via pytest-postgresql):
uv run pytest

# Run the app locally against a Postgres you provide:
export POSTGRESQL_DB_CONNECT_STRING="postgresql://user:pass@localhost:5432/standup"
./migrate.sh                         # create/upgrade the schema
uv run uvicorn app:app --port 8765   # or: uv run python -m standup_dashboard
```

## Prerequisites (one-time, need sudo)

```sh
sudo snap install rockcraft charmcraft --classic
sudo snap install k8s --classic          # Canonical Kubernetes
sudo k8s bootstrap
sudo k8s status --wait-ready
sudo k8s enable network dns local-storage ingress load-balancer
# Register the cluster with Juju and bootstrap a controller:
sudo k8s config | juju add-k8s ck8s --client
juju bootstrap ck8s
juju add-model standup
```

## Build

```sh
rockcraft pack                       # -> standup-dashboard_0.1_amd64.rock
charmcraft pack                      # -> standup-dashboard_amd64.charm

# Publish the rock to a registry the cluster can pull from. Canonical K8s has no
# built-in registry addon, so use a local registry / Docker Hub / GHCR:
skopeo --insecure-policy copy \
  oci-archive:standup-dashboard_0.1_amd64.rock \
  docker://<registry>/standup-dashboard:0.1
```

## Deploy & integrate

```sh
juju deploy ./standup-dashboard_amd64.charm \
  --resource app-image=<registry>/standup-dashboard:0.1

juju deploy postgresql-k8s --trust --channel 14/stable
juju integrate standup-dashboard postgresql-k8s

juju deploy traefik-k8s --trust
juju integrate standup-dashboard traefik-k8s

# API credentials via a Juju secret (keys surface as APP_SECRETS_<KEY>):
juju add-secret standup-creds \
  jira-token=<...> pagerduty-token=<...> \
  pagerduty-ical-url=<...> github-token=<...>
juju grant-secret standup-creds standup-dashboard
juju config standup-dashboard secrets=<secret-uri-from-add-secret>

# Optional non-secret config:
juju config standup-dashboard refresh-interval=1800 window-days=7
```

## Verify

```sh
juju status                          # all units active/idle
juju run traefik-k8s/0 show-proxied-endpoints   # the dashboard URL
# open the URL: the dashboard renders; the scheduler refreshes on its interval.
```

## Notes / gotchas

- **Existing local SQLite history is not migrated.** A fresh deploy starts empty
  and fills on the first refresh. A one-off `data/dashboard.db` → Postgres
  backfill script could be added if the history matters.
- **App import path**: the package is under `src/`; `app.py` puts `src` on
  `sys.path`, `migrate.sh` and the scheduler service set `PYTHONPATH=/app/src`.
  If `rockcraft pack` places the app somewhere other than `/app`, adjust the
  `PYTHONPATH` in `rockcraft.yaml` accordingly.
- **`requirements.txt`** is generated from `uv.lock`
  (`uv export --no-dev --no-emit-project --no-hashes -o requirements.txt`);
  regenerate it whenever dependencies change.
