# Implementation Plan: IS SRE Standup Dashboard

**Branch**: `001-sre-standup-dashboard` (feature directory; work proceeds on `main` — no separate git branch was created) | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-sre-standup-dashboard/spec.md`

## Summary

A single-user, locally-run web dashboard that gives an SRE manager at-a-glance, role-aware visibility of two-to-three regional squads before and during daily stand up. It pulls read-only data from Jira Cloud (ISDB roadmap + ISReq customer-request projects) and PagerDuty (incidents + weekend on-call iCal), classifies each engineer's current-sprint ("pulse") tickets into To Do / WIP / Success / Distractors, and colors each ticket green/yellow/red against the engineer's role-of-the-day. It renders a per-day pulse counts table (ticket throughput + alert load, timezone-bucketed per region, deduplicated across regions) and a per-engineer chip grid with click-to-open detail panels. All fetches are stored locally and never deleted, enabling future trend analysis. The app writes nothing back to Jira or PagerDuty.

**Technical approach**: A Python 3.12 application using **FastAPI** (async — to fan out the several external API calls concurrently on refresh) served by **uvicorn** on localhost. The UI is **server-rendered Jinja2 templates enhanced with HTMX** plus a small amount of vanilla JS (no build step) — a pragmatic fit for a personal, single-user, half-screen vertical layout with chips, multiple open detail panels, a schedule modal, and toggles. Persistence is **SQLite** (queryable history for future trends) alongside **raw JSON snapshot files** (full-fidelity audit of every fetch). External calls use **httpx**; the iCal feed is parsed with **icalendar**; timezones use the stdlib **zoneinfo**. Dependency/venv management is **uv**; tests are **pytest**.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, uvicorn, httpx (async HTTP client), Jinja2, HTMX (vendored JS, no build), icalendar (iCal parsing), pydantic (config + domain validation), PyYAML or TOML for static config (roster/regions). Dev: pytest, pytest-asyncio, respx (httpx mocking), ruff.

**Storage**: SQLite (via stdlib `sqlite3`) for normalized, queryable historical snapshots (tickets, alerts, fetch metadata, role schedule, overrides); raw JSON files under `data/snapshots/` for full-fidelity append-only fetch payloads. History is never deleted.

**Testing**: pytest + pytest-asyncio; respx to record/replay Jira & PagerDuty HTTP; deterministic fixtures for timezone/day-bucketing and the color matrix.

**Target Platform**: Local developer machine (Linux/macOS), accessed via a desktop browser at `http://localhost:8765` (default port; configurable). Offline-capable display of the last successful snapshot.

**Project Type**: Web application — single Python project serving an HTML UI (server-rendered templates + HTMX), not a separate frontend SPA.

**Performance Goals**: A manual refresh (both Jira projects + PagerDuty incidents + iCal) completes in under ~10 s for the modeled team size; rendering the dashboard from the latest stored snapshot is effectively instant (<300 ms server render). These are comfort targets, not hard SLAs, given single-user scale.

**Constraints**: Strictly read-only toward Jira/PagerDuty (no write/PUT/POST that mutates remote state). Credentials only from plain-text files under `secrets/` (never committed); `secrets/` is gitignored with committed example placeholders. Per-region timezone correctness (America/Mexico_City, Australia/Sydney, Europe/Paris) for day bucketing, role-day resolution, and override expiry. Cross-region dedup by ticket ID / alert ID.

**Scale/Scope**: 1 user (the manager). 3 regions, ~25 engineers + 2 global managers. 2 Jira projects on aligned 2-week sprints. Low hundreds of tickets and alerts per pulse. UI ≈ one vertical page + one schedule modal + N detail panels.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution (`.specify/memory/constitution.md`) is an **unfilled template** — it contains only placeholder principles with no ratified rules. There are therefore **no enforceable gates** to evaluate.

Applying sensible default discipline in the spirit of a constitution:

- **Simplicity / YAGNI**: Single project, no microservices, no build pipeline, stdlib-first (sqlite3, zoneinfo). PASS.
- **Read-only safety**: A hard project rule that no external client method issues mutating requests; enforced by client design + tests. PASS.
- **Testability**: Pure functions for the two highest-risk areas (role→color matrix, timezone day-bucketing/dedup) so they are unit-testable without network. PASS.
- **Observability**: Structured fetch logs + persisted fetch metadata (timestamp, per-source success/failure). PASS.

**Initial Constitution Check**: PASS (no gates defined; defaults satisfied).
**Post-Design Constitution Check**: PASS (re-evaluated after Phase 1 — see end of Phase 1; design introduces no new violations).

## Project Structure

### Documentation (this feature)

```text
specs/001-sre-standup-dashboard/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 output — stack & integration decisions
├── data-model.md        # Phase 1 output — entities, fields, relationships, state
├── quickstart.md        # Phase 1 output — setup & end-to-end validation guide
├── contracts/           # Phase 1 output
│   ├── internal-web.md  #   Local HTTP routes the UI calls
│   ├── jira.md          #   Consumed Jira Cloud REST surface (read-only)
│   └── pagerduty.md     #   Consumed PagerDuty REST + iCal surface (read-only)
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # /speckit-tasks output (NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/standup_dashboard/
├── __init__.py
├── __main__.py              # `python -m standup_dashboard` → launches uvicorn
├── app.py                   # FastAPI app factory, route registration, startup checks
├── config.py                # Static config: regions, timezones, roster, project keys, base URLs
├── settings.py              # Secrets loading from secrets/*.txt; startup validation
├── domain/
│   ├── models.py            # Dataclasses/pydantic: Engineer, Region, Ticket, Alert, etc.
│   ├── roles.py             # Role enum + pure effective-role resolution (override → weekly → weekend)
│   └── coloring.py          # Pure role×project→color matrix (incl. BVG strict, Success-green)
├── clients/
│   ├── jira.py              # Read-only Jira Cloud client (httpx): sprints, issues, changelog
│   ├── pagerduty.py         # Read-only PagerDuty client: incidents, log entries
│   └── ical.py              # Weekend on-call iCal fetch + parse
├── services/
│   ├── pulse.py             # Resolve active sprint per project ("pulse")
│   ├── touches.py           # Derive per-engineer ticket touches from changelog/comments/worklogs/links
│   ├── classification.py    # To Do / WIP / Success / Distractors grouping
│   ├── counts.py            # Per-day counts table; per-region tz bucketing; cross-region dedup
│   ├── schedule.py          # Weekly role defaults, today-only override (region-midnight expiry), BVG strict toggle
│   ├── oncall.py            # Weekend on-call resolution + Mon combined Sat/Sun
│   └── fetch.py             # Orchestrate refresh: fan-out fetch → persist snapshot
├── storage/
│   ├── db.py                # SQLite schema + read/write (history-preserving)
│   └── snapshots.py         # Append-only raw JSON snapshot writer
└── web/
    ├── routes.py            # FastAPI routes (page, refresh, chip detail, schedule modal, toggles)
    ├── presenters.py        # Build view models (chips, counts rows, panels) from stored data
    ├── templates/           # Jinja2: index, _chip, _detail_panel, _counts_table, _schedule_modal
    └── static/              # htmx.min.js, app.css, small app.js

data/                        # gitignored — sqlite db + raw JSON snapshots
├── dashboard.db
└── snapshots/

secrets/                     # gitignored — real tokens live here at runtime
secrets.example/             # COMMITTED placeholders showing required files
├── jira_token.txt
├── pagerduty_token.txt
└── pagerduty_ical_url.txt

tests/
├── unit/                    # coloring matrix, role resolution, tz bucketing/dedup, touch attribution
├── integration/            # fetch→persist→present with mocked HTTP (respx) + iCal fixtures
└── fixtures/               # canned Jira/PagerDuty payloads, iCal feeds

pyproject.toml               # uv-managed; deps + tool config
.gitignore                   # secrets/, data/, .venv, __pycache__
```

**Structure Decision**: Single Python project (Project Type: web application, server-rendered). Domain logic (`domain/`, `services/`) is deliberately separated from I/O (`clients/`, `storage/`) and presentation (`web/`) so the high-risk pure logic — the color matrix, role resolution, timezone day-bucketing, and cross-region deduplication — is unit-testable without any network or DB. The committed `secrets.example/` directory (rather than committing files inside the gitignored `secrets/`) satisfies "examples committed, real secrets never committed."

## Complexity Tracking

> No constitution gates are defined and the design introduces no violations requiring justification. This section is intentionally empty.
