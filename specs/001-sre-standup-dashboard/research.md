# Phase 0 Research: IS SRE Standup Dashboard

The spec deliberately left the implementation stack to planning. This document records the technology and integration decisions that resolve every "NEEDS CLARIFICATION" implied by the open Technical Context, with rationale and rejected alternatives.

---

## Decision 1 — Language & runtime: Python 3.12

- **Decision**: Python 3.12 (already installed; `uv` available for env/dep management).
- **Rationale**: The repository is already a Python project (PyCharm `.idea/`, a sample `main.py`, `uv 0.11`). Python has first-class libraries for every integration (Jira/PagerDuty REST via httpx, iCal via `icalendar`), stdlib `zoneinfo` for the required timezones, and stdlib `sqlite3` for history. Single-user local scope needs no heavier runtime.
- **Alternatives considered**: Node/TypeScript (also installed) — rejected because it adds a build toolchain and the repo signals are Python-first. Go — overkill for a single-user local tool and weaker iCal/Jira ergonomics.

## Decision 2 — Web framework: FastAPI + uvicorn

- **Decision**: FastAPI served by uvicorn, bound to localhost.
- **Rationale**: A refresh fans out several independent network calls (two Jira projects, PagerDuty incidents, iCal). FastAPI's async model + httpx lets these run concurrently, keeping refresh within the ~10 s comfort target. FastAPI also gives clean routing and dependency injection for the read/refresh/toggle endpoints, and trivially serves Jinja2 templates and static files.
- **Alternatives considered**: Flask — synchronous; concurrent fetch would need threads. Streamlit — fast for charts but a poor fit for the exact UI (single half-screen vertical page, multiple simultaneously-open detail panels, a schedule modal grid, conditional toggles, precise per-ticket color coding); fighting its layout model would cost more than it saves. Django — far too heavy for one user and one page.

## Decision 3 — Frontend approach: server-rendered Jinja2 + HTMX (no build step)

- **Decision**: Jinja2 templates with HTMX for partial updates (refresh, opening a chip's detail panel, toggling strict mode, saving schedule cells), plus minimal vanilla JS and a hand-written CSS file. HTMX is vendored as a static asset.
- **Rationale**: The UI is interactive but small and single-user. HTMX delivers click-to-open panels, modal interactions, and toggle round-trips against the FastAPI backend without a JS framework, bundler, or `node_modules`. The half-screen vertical layout and role color coding are straightforward CSS. Keeping color/role logic on the server (in tested Python) avoids duplicating the matrix in JS.
- **Alternatives considered**: React/Vue SPA — adds a build pipeline and a second language surface for logic that must stay consistent with the server; unjustified for one user. Pure server forms (no HTMX) — would force full-page reloads, breaking the "multiple panels open at once" requirement.

## Decision 4 — Persistence: SQLite (queryable history) + raw JSON snapshots (fidelity)

- **Decision**: A SQLite database (`data/dashboard.db`) holds normalized, queryable records (fetch metadata, tickets, alerts, role schedule, overrides). Each fetch also writes a full raw JSON payload to `data/snapshots/<timestamp>/`. Nothing is ever deleted.
- **Rationale**: The spec mandates permanent retention "to enable future trend analysis across pulses." SQLite makes that history queryable (counts per day, per pulse, per engineer) with zero external services and is stdlib. Raw JSON snapshots preserve everything the APIs returned, so future analyses aren't limited by today's schema. Append-only writes + a `fetched_at` stamp on every row satisfy "timestamped, never deleted."
- **Alternatives considered**: JSON files only — hard to query for trends. Postgres — needs a server; overkill for one local user. ORM (SQLAlchemy) — unnecessary; the schema is small and stable, so thin `sqlite3` helpers keep dependencies and complexity low.

## Decision 5 — HTTP client & external-API safety: httpx, read-only by construction

- **Decision**: Use `httpx.AsyncClient`. Jira and PagerDuty clients expose only GET-based read methods; there is no code path that issues a mutating request. A unit test asserts clients never call non-GET verbs.
- **Rationale**: FR-027 requires the tool be strictly read-only toward external sources. Enforcing this in client design (and a guard test) makes accidental writes structurally impossible rather than merely avoided by convention.
- **Alternatives considered**: `requests` — synchronous, doesn't compose with FastAPI's async fan-out. Official Jira/PagerDuty SDKs — heavier, and the read surface needed is small and well-documented; direct REST keeps control and testability.

## Decision 6 — Authentication to external services

- **Decision**: Jira Cloud via HTTP Basic auth using the account email (`fernando.carrillo.castro@canonical.com`) + the API token from `secrets/jira_token.txt`. PagerDuty via the `Authorization: Token token=<...>` header from `secrets/pagerduty_token.txt`. The weekend on-call iCal is fetched from the URL in `secrets/pagerduty_ical_url.txt` (a PagerDuty schedule iCal). All three are read at startup; a missing/empty file is a blocking setup error with a message naming the expected file.
- **Rationale**: Matches the documented credential files and Jira Cloud's standard email+token Basic auth and PagerDuty's REST token scheme. Centralizing secret loading at startup gives one clear failure point.
- **Alternatives considered**: OAuth flows — unnecessary for a personal token-based local tool. Env vars — the spec explicitly mandates per-secret plain-text files under `secrets/`.

## Decision 7 — Resolving the "pulse" (active sprint per project)

- **Decision**: For each project (ISDB, ISReq) resolve the active sprint via the Jira Agile API: find the board for the project, then query its active sprint(s). A ticket is "in pulse" if it is a member of its own project's active sprint. Sprint start/end dates define the per-day rows of the counts table.
- **Rationale**: Both projects run aligned 2-week sprints (Assumptions), but membership is still evaluated per project to honor "in pulse = active sprint of its own project." The Agile API exposes active sprint state and date boundaries directly.
- **Open follow-up (non-blocking)**: The board ID per project is discoverable at runtime by project key; if a project has multiple boards, prefer the board whose name/type matches the squad's working board. This can be confirmed during implementation against the live instance and, if needed, pinned in `config.py`.

## Decision 8 — Detecting "touched" tickets and Distractors

- **Decision**: Determine touches from Jira issue history within the pulse window. For each candidate issue, inspect: the changelog (`expand=changelog`) for status changes, assignment changes, and link additions; comments (with author + created timestamp); and worklogs (author + started timestamp). Any such action by an engineer inside the pulse window counts as a "touch." Candidate issues are gathered with JQL over both projects updated during the pulse window (`project in (ISDB, ISReq) AND updated >= <pulse start>`), supplemented by issues where the engineer is or was assignee. A touched issue that is not assigned to that engineer in the active sprint — or belongs to a different sprint — becomes a Distractor for them.
- **Rationale**: Jira has no single JQL predicate for "touched by any of {status change, comment, assignment change, worklog, link add}", so the reliable path is to pull candidate issues changed in the window and attribute actions per engineer from changelog/comments/worklogs. This directly implements FR-014's five touch types.
- **Alternatives considered**: Pure JQL (`worklogAuthor`, `commentedBy`, `assignee was`) — can't express link additions and is awkward to union across five action types; the changelog-inspection approach is comprehensive and keeps attribution logic in tested Python. Cost is more API reads, acceptable at this scale and cached in snapshots.

## Decision 9 — Timezones, day bucketing, and cross-region dedup

- **Decision**: Use stdlib `zoneinfo` with America/Mexico_City (AMER), Australia/Sydney (APAC), Europe/Paris (EMEA). All bucketing is a pure function: given UTC event timestamps and a region zone, assign each to a local calendar day. Effective-role resolution and override expiry also use the engineer's region zone (per clarification). When regions are combined, tickets are deduplicated by ticket ID and alerts by alert ID before summing, and each region is bucketed in its own zone first.
- **Rationale**: Clarifications fixed region-timezone as the authority for both day bucketing and role/override day. Keeping bucketing/dedup as pure functions over explicit inputs makes the date-line edge cases unit-testable deterministically.
- **Alternatives considered**: Single global timezone — contradicts the per-region requirement. `pytz` — superseded by stdlib `zoneinfo` in 3.9+.

## Decision 10 — Weekend on-call and the Monday combined row

- **Decision**: Parse the iCal feed for the event covering the weekend to identify the single on-call engineer (matched to a roster engineer by name/email). On Saturday/Sunday all other engineers are treated as OFF; on Monday the dashboard shows the on-call engineer's combined Saturday+Sunday activity as one weekend row/section.
- **Rationale**: Implements FR-025 directly. `icalendar` robustly parses the feed; matching the summary/attendee to the roster yields the on-call identity.
- **Alternatives considered**: PagerDuty on-call REST endpoint — viable, but the spec specifically names an iCal feed as the source, so we honor that; the REST endpoint can be a future fallback.

## Decision 11 — Configuration & secrets layout

- **Decision**: Static, non-secret configuration (regions, timezones, the roster with names/emails, project keys, Jira base URL, account email, role/color rules) lives in committed `config.py`/a config file. Secrets live only in `secrets/*.txt` (gitignored). Committed placeholders live in `secrets.example/` with the same filenames. `.gitignore` excludes `secrets/` and `data/`.
- **Rationale**: Satisfies "examples committed, real secrets never committed, secrets/ gitignored." Putting examples in a sibling `secrets.example/` avoids the trap of committing files that live inside an ignored directory. The quickstart instructs copying `secrets.example/* → secrets/`.
- **Alternatives considered**: Committing `secrets/*.txt.example` inside `secrets/` while ignoring only `secrets/*.txt` — workable but error-prone (easy to accidentally commit a real token); a separate example dir is safer.

## Decision 12 — Testing strategy

- **Decision**: pytest + pytest-asyncio. Pure-logic unit tests for the color matrix (every role×project×strict/Success combination), role/override resolution, timezone bucketing, cross-region dedup, and touch attribution. Integration tests run fetch→persist→present against `respx`-mocked Jira/PagerDuty HTTP and static iCal fixtures. A guard test asserts external clients issue only read requests.
- **Rationale**: The correctness-critical, high-combinatorial areas (color rules, timezone/dedup) are pure functions and deserve exhaustive table-driven tests; network is mocked so tests are fast and deterministic.
- **Alternatives considered**: End-to-end browser tests — disproportionate for a single-user local tool in v1; the HTMX surface is thin and can be smoke-checked via the quickstart.

---

## Resolved unknowns summary

| Technical Context field | Resolution |
|---|---|
| Language/Version | Python 3.12 |
| Primary Dependencies | FastAPI, uvicorn, httpx, Jinja2, HTMX, icalendar, pydantic |
| Storage | SQLite + append-only raw JSON snapshots |
| Testing | pytest, pytest-asyncio, respx |
| Target Platform | Local machine, desktop browser at localhost |
| Project Type | Web application (single project, server-rendered) |
| Performance Goals | Refresh < ~10 s; render < ~300 ms (single-user scale) |
| Constraints | Read-only external; secrets in files; per-region tz; dedup by id |
| Scale/Scope | 1 user, 3 regions, ~27 people, 2 projects, hundreds of items/pulse |

No unresolved NEEDS CLARIFICATION remain. Two non-blocking runtime confirmations (board selection per project in Decision 7; on-call name/email matching in Decision 10) are to be verified against the live instance during implementation and pinned in config if needed.
