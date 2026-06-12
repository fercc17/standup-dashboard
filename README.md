# IS SRE Standup Dashboard

A single-user, locally-run web dashboard that gives an SRE manager at-a-glance,
role-aware visibility of two-to-three regional squads before and during daily
stand-up. It reads **read-only** from Jira Cloud (ISDB + ISReq) and PagerDuty
(incidents + weekend on-call iCal), classifies each engineer's current-sprint
("pulse") tickets into To Do / WIP / Success / Distractors, colors each ticket
against the engineer's role-of-the-day, and renders a per-day pulse counts
table. Every fetch is stored locally and never deleted. The app writes nothing
back to Jira or PagerDuty.

Stack: Python 3.12 · FastAPI + uvicorn · Jinja2 + HTMX (no build) · httpx ·
SQLite + raw JSON snapshots · icalendar · zoneinfo · uv · pytest.

## Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/) installed.
- A Jira Cloud API token, a PagerDuty API token, and a PagerDuty schedule iCal URL.
- Network access to `https://warthogs.atlassian.net` and PagerDuty.

## 1. Install

```bash
uv sync                      # create venv + install deps from pyproject.toml
```

## 2. Provide credentials (never committed)

`secrets/` and `data/` are gitignored; `secrets.example/` holds committed
placeholders. Copy them in and fill each file with **only** the real value:

```bash
cp -r secrets.example/. secrets/
#   secrets/jira_token.txt        -> Jira API token
#   secrets/pagerduty_token.txt   -> PagerDuty API token
#   secrets/jira_ical_url.txt     -> PagerDuty weekend on-call iCal URL
```

Confirm nothing secret is tracked:

```bash
git check-ignore secrets/jira_token.txt data/dashboard.db   # both should print
```

A missing or empty secret file, or a roster engineer with no matching PagerDuty
identity, produces a blocking **setup page** naming the problem instead of the
dashboard.

## 3. Run

```bash
uv run python -m standup_dashboard        # serves http://localhost:8765
```

Open `http://localhost:8765`, pick one or more regions (AMER / APAC / EMEA), and
click **Refresh** to perform the first fetch.

## Using the dashboard

- **Region buttons** toggle regions; selecting several groups chips under
  per-region headers and combines the counts table (deduplicated by ticket /
  alert id). Global managers appear under a **Global** group, excluded from totals.
- **Chips** show each engineer's name, role-of-the-day (color-coded), tickets
  touched in 24h, and alerts ack/resolved in 24h. Click a chip to open a detail
  panel; multiple panels stay open at once.
- **Schedule** opens a Mon–Fri + Weekend grid to set weekly default roles plus a
  today-only override (expires at the engineer's region-local midnight).
- **BVG strict** toggle appears only when a BVG engineer exists today and
  tightens BVG ISReq coloring (green only for Highest / `ps5-blockers`).
- The **counts table** shows one row per pulse day (Monday combines Saturday +
  Sunday), bucketed in each region's timezone.

## Validate

```bash
uv run pytest -q                  # unit + integration (HTTP mocked with respx)
uv run pytest -q -k read_only     # guard: external clients issue only GET
uv run ruff check .
```

End-to-end acceptance scenarios S1–S7 are in
[`specs/001-sre-standup-dashboard/quickstart.md`](specs/001-sre-standup-dashboard/quickstart.md).

## Layout

```text
src/standup_dashboard/
├── config.py / settings.py   # static roster/regions; secrets loading
├── domain/                   # pure logic: models, role resolution, color matrix
├── clients/                  # read-only Jira / PagerDuty / iCal (GET only)
├── services/                 # pulse, touches, classification, counts, schedule, oncall, fetch
├── storage/                  # SQLite (history-preserving) + raw JSON snapshots
└── web/                      # FastAPI routes, presenters, Jinja2 templates, static
data/                         # gitignored — dashboard.db + snapshots/
secrets/ | secrets.example/   # real (gitignored) | committed placeholders
```
