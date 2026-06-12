---
description: "Task list for IS SRE Standup Dashboard implementation"
---

# Tasks: IS SRE Standup Dashboard

**Input**: Design documents from `/specs/001-sre-standup-dashboard/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Targeted tests ARE included. The spec did not request full TDD, but `plan.md` (research Decision 12) and `quickstart.md` explicitly require unit coverage of the high-risk pure logic (color matrix, role/override resolution, timezone bucketing, cross-region dedup, touch attribution), a read-only guard test, and per-story integration tests. Test tasks are scoped to those areas, not every task.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US6)
- All paths are relative to repository root `/home/fer/projects/standup-dashboard/`

## Path Conventions

Single Python project: package at `src/standup_dashboard/`, tests at `tests/`, per `plan.md` → Project Structure.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Initialize uv project and author `pyproject.toml` at repo root with deps (fastapi, uvicorn, httpx, jinja2, icalendar, pydantic) and dev deps (pytest, pytest-asyncio, respx, ruff); run `uv sync`
- [X] T002 [P] Create package skeleton: `src/standup_dashboard/{__init__.py,domain/__init__.py,clients/__init__.py,services/__init__.py,storage/__init__.py,web/__init__.py}`, `src/standup_dashboard/web/templates/`, `src/standup_dashboard/web/static/`, and `tests/{unit,integration,fixtures}/`
- [X] T003 [P] Configure ruff + pytest (incl. pytest-asyncio mode) in `pyproject.toml`
- [X] T004 [P] Create `.gitignore` at repo root excluding `secrets/`, `data/`, `.venv/`, `__pycache__/`
- [X] T005 [P] Create committed placeholders `secrets.example/jira_token.txt`, `secrets.example/pagerduty_token.txt`, `secrets.example/pagerduty_ical_url.txt` (FR-030)
- [X] T006 [P] Vendor `htmx.min.js` and create base `app.css` + `app.js` in `src/standup_dashboard/web/static/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T007 Implement static configuration in `src/standup_dashboard/config.py`: regions + IANA timezones (AMER→America/Mexico_City, APAC→Australia/Sydney, EMEA→Europe/Paris), full roster (names/emails, manager + global flags, region membership incl. Fernando in AMER+APAC), project keys ISDB/ISReq, Jira base URL, Jira account email (FR-001–FR-004)
- [X] T008 [P] Implement domain dataclasses in `src/standup_dashboard/domain/models.py`: Region, Engineer, Role enum, Ticket, TouchEvent, Alert, Pulse, WeekendOnCall, FetchSnapshot, and view models (ChipVM, CountsRow, DetailPanelVM) per data-model.md
- [X] T009 Implement SQLite schema + history-preserving access in `src/standup_dashboard/storage/db.py`: tables `fetch_snapshot, ticket, touch_event, alert, pulse, weekend_oncall, role_schedule, role_override, ui_state`; append-only writes keyed by `fetch_id`; never update/delete fetched rows (FR-028)
- [X] T010 [P] Implement append-only raw JSON snapshot writer in `src/standup_dashboard/storage/snapshots.py` (writes `data/snapshots/<fetched_at>/`)
- [X] T011 [P] Implement secrets loading + validation in `src/standup_dashboard/settings.py`: read `secrets/jira_token.txt`, `secrets/pagerduty_token.txt`, `secrets/pagerduty_ical_url.txt`; missing/empty file → structured blocking setup error naming the file (FR-029)
- [X] T012 Implement FastAPI app factory + startup checks + Jinja2/static mounts in `src/standup_dashboard/app.py` and entrypoint `src/standup_dashboard/__main__.py` (uvicorn bound to `localhost:8765`; single-user, no authentication layer per FR-011)
- [X] T013 Implement setup-page renderer + structured logging/error-handling scaffold in `src/standup_dashboard/web/routes.py` (serves blocking setup page when startup validation fails) and wire logging in `app.py`

**Checkpoint**: Foundation ready — user stories can begin.

> **Note**: The app can serve the setup/error page after Phase 2, but a *full* boot to the dashboard requires the FR-005a PagerDuty identity gate, which lands in US1 (T020 → T021) because it depends on the PagerDuty client. Until then, expect the setup page when identity validation runs.

---

## Phase 3: User Story 1 - See whether each engineer is working on the right thing (Priority: P1) 🎯 MVP

**Goal**: For a selected region, render one chip per engineer (name, role-of-day color, tickets-touched-24h, alerts-24h) and a click-to-open detail panel classifying tickets into To Do / WIP / Success / Distractors with role-aware color coding.

**Independent Test**: With roles seeded and one region selected, refresh → every engineer shows a correctly-colored chip; clicking a Project-role engineer shows ISDB green / ISReq red / non-assigned touch red, Done always green; multiple panels stay open.

### Tests for User Story 1

- [X] T014 [P] [US1] Unit tests for the color matrix (every role × project × strict × Success combination, FR-016/017) in `tests/unit/test_coloring.py`
- [X] T015 [P] [US1] Unit tests for effective-role resolution (override → weekly → weekend, region tz) in `tests/unit/test_roles.py`
- [X] T016 [P] [US1] Read-only guard test asserting Jira/PagerDuty clients issue only GET requests (FR-027) in `tests/unit/test_read_only.py`

### Implementation for User Story 1

- [X] T017 [P] [US1] Implement Role enum + effective-role resolution (override → weekly default → weekend; region-timezone "today") in `src/standup_dashboard/domain/roles.py` (FR-009)
- [X] T018 [P] [US1] Implement pure color-matrix function in `src/standup_dashboard/domain/coloring.py` (FR-016/017, BVG strict param, Success-always-green)
- [X] T019 [US1] Implement read-only Jira client in `src/standup_dashboard/clients/jira.py`: active sprint per project, sprint issues with `expand=changelog`, JQL search, comments, worklogs (contracts/jira.md)
- [X] T020 [US1] Implement read-only PagerDuty client + email→user identity resolution in `src/standup_dashboard/clients/pagerduty.py`: users lookup, incidents, log_entries (contracts/pagerduty.md)
- [X] T021 [US1] Wire FR-005a blocking identity validation at startup (every roster email must match a PagerDuty user; unmatched → setup page naming them) in `src/standup_dashboard/settings.py` + `src/standup_dashboard/app.py`
- [X] T022 [P] [US1] Implement active-sprint ("pulse") resolution per project in `src/standup_dashboard/services/pulse.py` (FR-012)
- [X] T023 [US1] Implement touch attribution (status/comment/assignment/worklog/link within pulse window) in `src/standup_dashboard/services/touches.py` (FR-014; depends on T019)
- [X] T024 [US1] Implement ticket classification into To Do/WIP/Success/Distractors in `src/standup_dashboard/services/classification.py`, including detection of the `[PR/MP Review]` ISReq title-prefix as the BVG review ticket type and tagging it on the Ticket model (FR-013, FR-015; depends on T022, T023)
- [X] T025 [US1] Implement refresh orchestration (Jira + PagerDuty async fan-out → persist SQLite + raw snapshot) in `src/standup_dashboard/services/fetch.py` (FR-026; depends on T019, T020, T009, T010)
- [X] T026 [US1] Implement chip + detail view models (apply coloring, 24h touch/alert counts, surface `[PR/MP Review]` BVG type label) in `src/standup_dashboard/web/presenters.py` (FR-018/019, FR-015; depends on T018, T024)
- [X] T027 [P] [US1] Create Jinja2 templates `index.html`, `_chip.html`, `_detail_panel.html` in `src/standup_dashboard/web/templates/` (half-screen vertical layout, top bar with region buttons + refresh + last-fetch timestamp)
- [X] T028 [US1] Implement routes `GET /`, `POST /refresh`, `GET /chip/{engineer_email}/detail` in `src/standup_dashboard/web/routes.py` (contracts/internal-web.md; additive panels via HTMX; depends on T025, T026, T027)
- [X] T029 [US1] Integration test fetch→persist→render chips + detail panel with respx-mocked Jira/PagerDuty in `tests/integration/test_us1_activity.py`

**Checkpoint**: MVP — a single-region, role-aware activity view is fully functional.

---

## Phase 4: User Story 2 - Set and adjust engineer roles (Priority: P1)

**Goal**: A schedule modal (Mon–Fri + Weekend grid) to set weekly default roles, a today-only override row that expires at region-local midnight, and a BVG strict-mode toggle visible only when a BVG engineer exists today.

**Independent Test**: Set a weekly role → persists across reload; set a today override → chip reflects it, weekly default unchanged, gone after local midnight; strict toggle flips BVG non-Highest/non-`ps5-blockers` ISReq green↔yellow without editing config.

### Tests for User Story 2

- [X] T030 [P] [US2] Unit tests for override expiry at region-local midnight, weekly fallback, and strict-mode effect on BVG coloring in `tests/unit/test_role_schedule.py`

### Implementation for User Story 2

- [X] T031 [US2] Implement schedule service in `src/standup_dashboard/services/schedule.py`: set weekly default, set today-only override with region-midnight `expires_at`, get/set BVG strict mode via `ui_state` (FR-007/008/010; uses storage/db.py)
- [X] T032 [P] [US2] Create `_schedule_modal.html` (per-engineer Mon–Fri + Weekend dropdowns + today-override row) in `src/standup_dashboard/web/templates/`
- [X] T033 [US2] Implement routes `GET /schedule`, `POST /schedule/weekly`, `POST /schedule/override`, `POST /toggle/strict` in `src/standup_dashboard/web/routes.py` (contracts/internal-web.md; depends on T031, T032)
- [X] T034 [US2] Integrate strict-mode + persisted roles into chip/coloring presenters and make the strict toggle conditionally visible (only when a BVG engineer exists today) in `src/standup_dashboard/web/presenters.py` (FR-010/032; depends on T026, T031)
- [X] T035 [US2] Integration test schedule persistence + override expiry + strict-toggle recolor in `tests/integration/test_us2_roles.py`

**Checkpoint**: Roles drive the US1 view end-to-end; US1 + US2 both work.

---

## Phase 5: User Story 3 - Read sprint throughput and alert load for the pulse (Priority: P2)

**Goal**: A counts table with one row per pulse day (Monday combines Sat+Sun) and the nine FR-021 columns, bucketed in the region's timezone.

**Independent Test**: With one region selected, the table shows one row per pulse day with all nine columns, the Monday row combines Saturday+Sunday, and days bucket in the region's timezone.

### Tests for User Story 3

- [X] T036 [P] [US3] Unit tests for per-day bucketing, the nine columns, and Monday weekend-combine in `tests/unit/test_counts.py`

### Implementation for User Story 3

- [X] T037 [US3] Extend Jira client with counts queries (open Highest ISReq snapshot, new Highest 24h, open/new `ps5-blockers`, ISDB completed-that-day) in `src/standup_dashboard/clients/jira.py` (contracts/jira.md §4)
- [X] T038 [US3] Implement counts service in `src/standup_dashboard/services/counts.py`: per-day rows, nine columns, region-timezone bucketing, Monday weekend row, alert ack/resolved/total aggregation (FR-020/021/022/023)
- [X] T039 [US3] Implement CountsRow presenter and `_counts_table.html` template in `src/standup_dashboard/web/presenters.py` + `src/standup_dashboard/web/templates/`
- [X] T040 [US3] Wire the counts table into `GET /` and `POST /refresh` in `src/standup_dashboard/web/routes.py` (depends on T038, T039)
- [X] T041 [US3] Integration test counts-table rows/columns/weekend-combine with mocked data in `tests/integration/test_us3_counts.py`

**Checkpoint**: Single-region counts table renders alongside chips.

---

## Phase 6: User Story 4 - Combine multiple regions without double-counting (Priority: P2)

**Goal**: Multi-region selection groups chips under per-region headers (manager once per owned region), deduplicates tickets by id and alerts by id in combined counts, buckets each region in its own tz, and uses the deduplicated AMER+APAC+EMEA denominator for the alert %. Global managers render under a "Global" group, excluded from totals.

**Independent Test**: Select AMER+APAC → Fernando appears once under each; a shared ticket/alert counts once in the combined table; the region % uses the deduplicated three-region denominator; Global managers appear but aren't in totals.

### Tests for User Story 4

- [X] T042 [P] [US4] Unit tests for cross-region dedup (ticket id / alert id), multi-region-manager-counted-once, Global exclusion, and denominator in `tests/unit/test_multiregion.py`

### Implementation for User Story 4

- [X] T043 [US4] Extend counts service with cross-region combine + dedup by ticket/alert id + deduplicated three-region denominator in `src/standup_dashboard/services/counts.py` (FR-024)
- [X] T044 [US4] Implement per-region chip grouping headers, manager-once-per-region, and dedicated "Global" group (excluded from totals) in `src/standup_dashboard/web/presenters.py` + templates (FR-004/005)
- [X] T045 [US4] Handle multi-value `regions` selection in `GET /` and `POST /refresh` (per-region current local day display) in `src/standup_dashboard/web/routes.py` (FR-002/032)
- [X] T046 [US4] Integration test AMER+APAC combined dedup + Global group in `tests/integration/test_us4_multiregion.py`

**Checkpoint**: Multi-region combined view is correct and de-duplicated.

---

## Phase 7: User Story 5 - Account for weekend on-call coverage (Priority: P3)

**Goal**: Resolve the single weekend on-call engineer from the iCal feed; treat all others as OFF Sat/Sun; on Monday show the on-call engineer's combined Saturday+Sunday activity.

**Independent Test**: With an iCal fixture naming an on-call engineer, on Monday only that engineer shows weekend activity (others OFF) and their Sat+Sun is combined.

### Tests for User Story 5

- [X] T047 [P] [US5] Unit tests for on-call resolution, others-OFF, and Monday Sat+Sun combine in `tests/unit/test_oncall.py`

### Implementation for User Story 5

- [X] T048 [US5] Implement read-only iCal client in `src/standup_dashboard/clients/ical.py` (fetch + `icalendar` parse; contracts/pagerduty.md §3)
- [X] T049 [US5] Implement on-call service in `src/standup_dashboard/services/oncall.py`: resolve weekend on-call → roster match, others OFF Sat/Sun, Monday combined weekend (FR-025; depends on T048)
- [X] T050 [US5] Integrate on-call into `services/fetch.py` (fetch iCal), the counts weekend row, and chip role resolution in `src/standup_dashboard/web/presenters.py`
- [X] T051 [US5] Integration test Monday combined-weekend view with iCal fixture in `tests/integration/test_us5_oncall.py`

**Checkpoint**: Monday stand-ups attribute weekend coverage correctly.

---

## Phase 8: User Story 6 - Retain history for future trend analysis (Priority: P3)

**Goal**: Every fetch is timestamped and stored; nothing is ever deleted; per-source success flags drive a partial-outage indicator and last-good fallback; zero writes to external systems.

**Independent Test**: Refresh twice → two timestamped snapshots exist and the earlier persists; on a simulated source outage the last good data shows with a failure banner; no write request is issued externally.

### Tests for User Story 6

- [X] T052 [P] [US6] Integration test: two fetches retained + per-source ok flags + no-delete invariant in `tests/integration/test_us6_history.py`

### Implementation for User Story 6

- [X] T053 [US6] Enforce append-only retention + per-source `jira_ok/pagerduty_ok/ical_ok` flags on `fetch_snapshot`, with last-good fallback, in `src/standup_dashboard/services/fetch.py` + `src/standup_dashboard/storage/db.py` (FR-028)
- [X] T054 [US6] Implement partial-outage UI indicator + last-fetch fallback rendering in `src/standup_dashboard/web/routes.py` + templates (Edge Cases)

**Checkpoint**: History is durable and read-only safety is observable.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Improvements spanning multiple stories

- [X] T055 [P] Write `README.md` with setup (copy `secrets.example/` → `secrets/`), run, and validation instructions
- [X] T056 [P] Add edge-case unit tests (no active pulse, missing credentials, engineer with zero activity, `[PR/MP Review]` ISReq type) in `tests/unit/test_edge_cases.py`
- [X] T057 [P] Run `ruff check`/format cleanup across `src/` and `tests/`
- [X] T058 Run `quickstart.md` scenarios S1–S7 end-to-end and fix any gaps

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. BLOCKS all user stories.
- **User Stories (Phases 3–8)**: All depend on Foundational. US1 (MVP) first; US2 depends on US1's presenters/coloring; US3 builds on US1's fetch/storage; US4 extends US3's counts + US1's presenters; US5 extends fetch + counts + role resolution; US6 hardens fetch/storage. Within priority order P1 → P1 → P2 → P2 → P3 → P3.
- **Polish (Phase 9)**: After desired stories complete.

### User Story Dependencies

- **US1 (P1)**: After Foundational. The MVP; no dependency on other stories (uses a default/seeded schedule until US2).
- **US2 (P1)**: After US1 (reuses `presenters.py` + `coloring.py`).
- **US3 (P2)**: After US1 (reuses `fetch.py`, `clients/jira.py`, storage).
- **US4 (P2)**: After US3 (extends `counts.py`) and US1 (extends `presenters.py`).
- **US5 (P3)**: After US1 (fetch) and US3 (counts weekend row).
- **US6 (P3)**: After US1 (fetch/storage).

### Within Each User Story

- Tests (where present) are written to fail first, then implementation.
- Pure domain logic (roles, coloring) → clients → services → presenters → routes/templates → integration test.

### Parallel Opportunities

- Setup: T002–T006 are [P].
- Foundational: T008, T010, T011 are [P] (distinct files); T009/T012/T013 are sequential (shared db/app/routes).
- US1: tests T014–T016 [P]; domain T017–T018 [P]; T022 and template T027 [P]. Clients/services that share files run sequentially.
- Each story's unit-test task ([P]) and template tasks ([P]) can run alongside its domain work.
- After Foundational, with multiple developers US1/US3/US5 backbone work can progress in parallel where files don't overlap, but US2/US4 should follow their parents to avoid `presenters.py`/`counts.py` conflicts.

---

## Parallel Example: User Story 1

```bash
# Tests first (independent files):
Task: "Unit tests for the color matrix in tests/unit/test_coloring.py"
Task: "Unit tests for effective-role resolution in tests/unit/test_roles.py"
Task: "Read-only guard test in tests/unit/test_read_only.py"

# Then pure domain logic in parallel:
Task: "Implement role resolution in src/standup_dashboard/domain/roles.py"
Task: "Implement color matrix in src/standup_dashboard/domain/coloring.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & VALIDATE** (quickstart S1) → demo a single-region, role-aware activity view.

### Incremental Delivery

Foundation → US1 (MVP, S1) → US2 (S2) → US3 (S3) → US4 (S4) → US5 (S5) → US6 (S6/S7) → Polish. Each story is an independently testable increment that doesn't break earlier ones.

### Parallel Team Strategy

After Foundational: one developer drives US1→US2 (presenter/coloring lineage), another preps US3→US4 (counts lineage), a third preps US5 (iCal/on-call). Integrate per checkpoint.

---

## Notes

- [P] = different files, no incomplete dependencies.
- [Story] label maps each task to a user story for traceability.
- Read-only toward Jira/PagerDuty is enforced by client design + the T016 guard test (FR-027).
- Secrets live only in gitignored `secrets/`; `secrets.example/` is the committed template (FR-029/030).
- Commit after each task or logical group; stop at any checkpoint to validate independently.
