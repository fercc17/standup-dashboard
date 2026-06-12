# Phase 1 Data Model: IS SRE Standup Dashboard

Derived from the spec's Key Entities and Functional Requirements. Two layers are described:

1. **Domain model** — in-memory objects used by services/presentation (the conceptual entities).
2. **Persistence model** — SQLite tables (history-preserving) + raw JSON snapshots.

All timestamps are stored in UTC (ISO-8601) and converted to a region timezone only at presentation/bucketing time.

---

## 1. Domain model

### Region

Static configuration (not fetched).

| Field | Type | Notes |
|---|---|---|
| `key` | enum | `AMER` \| `APAC` \| `EMEA` |
| `timezone` | str (IANA) | `America/Mexico_City` / `Australia/Sydney` / `Europe/Paris` |
| `manager_email` | str | The region's manager (also an Engineer) |
| `member_emails` | list[str] | Engineers in the squad, including the manager |

### Engineer

Static configuration (roster).

| Field | Type | Notes |
|---|---|---|
| `email` | str (PK) | Canonical identity; join key across Jira & PagerDuty (FR-005a) |
| `name` | str | Display name |
| `region_keys` | list[enum] | One or more regions; managers may span regions (e.g. Fernando: AMER+APAC) |
| `is_manager` | bool | True for region managers |
| `is_global` | bool | True for global management (Kristofer, Alexandre Micouleau) — shown under "Global", excluded from counts |

- **Identity rule**: every roster `email` MUST resolve to a PagerDuty user; an unmatched engineer is a blocking startup error (FR-005a).

### Role (enum) & RoleColorRule

`Role ∈ { PVG, BVG, GEN, Project, OFF }`.

Coloring is a **pure function** `color(role, project, ticket, strict_mode, group) -> {green, yellow, red}` implementing FR-016/FR-017:

| Role | assigned ISReq | assigned ISDB | non-assigned touch |
|---|---|---|---|
| PVG | red | red | green |
| BVG | green *(strict: green iff Highest or `ps5-blockers`, else yellow)* | red | green |
| GEN | green iff Highest or `ps5-blockers`, else red | red | red |
| Project | red | green | red |
| OFF | red | red | red |

- **Override rule**: Success group (status Done) is **always green**, regardless of role.

### RoleSchedule (weekly default) & RoleOverride (today-only)

| Entity | Field | Type | Notes |
|---|---|---|---|
| RoleSchedule | `engineer_email` | str | FK Engineer |
| | `weekday` | enum | `MON..FRI` + `WEEKEND` |
| | `role` | enum Role | Weekly default for that slot |
| RoleOverride | `engineer_email` | str | FK Engineer |
| | `role` | enum Role | Supersedes weekly default for one day |
| | `effective_date` | date | The region-local day it applies to |
| | `expires_at` | datetime (UTC) | Midnight of the next day in the engineer's region timezone |

- **Effective-role resolution** (FR-009): for an engineer on a given region-local day → active non-expired override if present, else weekly default for that weekday, else `WEEKEND` rule on Sat/Sun. The region timezone defines "today" and the weekday.

### Pulse (active sprint, per project)

| Field | Type | Notes |
|---|---|---|
| `project_key` | enum | `ISDB` \| `ISReq` |
| `sprint_id` | int | Jira sprint id |
| `name` | str | Sprint name |
| `start` / `end` | datetime (UTC) | Defines the per-day rows of the counts table |
| `state` | str | `active` |

- A ticket is **in pulse** iff it is a member of its own project's active sprint (FR-012).

### Ticket

Fetched from Jira; snapshot per fetch.

| Field | Type | Notes |
|---|---|---|
| `id` | str (PK within fetch) | Jira issue key, e.g. `ISReq-1234` — also the cross-region dedup key |
| `project_key` | enum | `ISDB` \| `ISReq` |
| `title` | str | `[PR/MP Review]` prefix on ISReq marks the BVG review type (FR-015) |
| `status` | str | Mapped to group: ToDo{To Do,Untriaged,Blocked} / WIP{In Progress,In Review} / Success{Done} |
| `priority` | str | `Highest` is significant for GEN/BVG-strict |
| `labels` | list[str] | `ps5-blockers` is significant |
| `assignee_email` | str \| null | Current assignee |
| `sprint_id` | int \| null | Sprint membership (for in-pulse vs different-sprint) |
| `is_done_date` | date \| null | The day it reached Done (for "ISDB completed that day") |

- **Classification (FR-013)** per engineer E: **To Do/WIP/Success** = assigned to E, in pulse, by status group; **Distractors** = touched by E during pulse but not assigned to E in this sprint, or from a different sprint.

### TouchEvent

Derived from changelog/comments/worklogs/links.

| Field | Type | Notes |
|---|---|---|
| `ticket_id` | str | FK Ticket |
| `engineer_email` | str | Who acted |
| `kind` | enum | `status` \| `comment` \| `assignment` \| `worklog` \| `link` (FR-014) |
| `at` | datetime (UTC) | Used for "touched during pulse" and "last 24h" windows |

### Alert

Fetched from PagerDuty; snapshot per fetch.

| Field | Type | Notes |
|---|---|---|
| `id` | str (PK) | Incident id — cross-region dedup key |
| `handler_email` | str | Region member who acknowledged/resolved |
| `state` | enum | `acknowledged` \| `resolved` |
| `at` | datetime (UTC) | Bucketed to a day per region timezone |

### WeekendOnCall

| Field | Type | Notes |
|---|---|---|
| `engineer_email` | str | The single weekend on-call (from iCal), matched to roster |
| `weekend_start` / `weekend_end` | date | Sat/Sun covered |

### FetchSnapshot

| Field | Type | Notes |
|---|---|---|
| `id` | int (PK) | Autoincrement |
| `fetched_at` | datetime (UTC) | "last successful fetch" timestamp shown in UI |
| `jira_ok` / `pagerduty_ok` / `ical_ok` | bool | Per-source success (for partial-outage messaging) |
| `raw_path` | str | Path to `data/snapshots/<ts>/` raw JSON |

### Derived view models (presentation only — not persisted)

- **ChipVM**: engineer name, effective role + color, tickets-touched-last-24h count, alerts-last-24h (ack/resolved), region grouping.
- **CountsRow**: one per pulse day (Monday combines Sat+Sun) with the nine FR-021 columns, region-bucketed and cross-region-deduplicated.
- **DetailPanelVM**: the four ticket groups for an engineer with per-ticket color applied.

---

## 2. Persistence model (SQLite — history-preserving)

Every fetched-data row carries `fetch_id` (FK → `fetch_snapshot`); rows are **appended, never updated or deleted**, so each fetch is a full historical layer queryable for trends.

```text
fetch_snapshot(id, fetched_at, jira_ok, pagerduty_ok, ical_ok, raw_path)

ticket(fetch_id, id, project_key, title, status, priority, labels_json,
       assignee_email, sprint_id, is_done_date)            -- PK (fetch_id, id)

touch_event(fetch_id, ticket_id, engineer_email, kind, at) -- PK (fetch_id, ticket_id, engineer_email, kind, at)

alert(fetch_id, id, handler_email, state, at)              -- PK (fetch_id, id)

pulse(fetch_id, project_key, sprint_id, name, start, end, state)

weekend_oncall(fetch_id, engineer_email, weekend_start, weekend_end)
```

State/config tables (current state, history kept via row versioning):

```text
role_schedule(engineer_email, weekday, role, updated_at)   -- weekly defaults; latest row wins per (engineer, weekday)

role_override(engineer_email, role, effective_date, expires_at, created_at)

ui_state(key, value, updated_at)                           -- e.g. bvg_strict_mode = on/off
```

- **Latest-snapshot read pattern**: the dashboard reads the most recent `fetch_snapshot` for display; trend analyses scan across `fetch_id` history.
- **Raw snapshots**: `data/snapshots/<fetched_at>/{jira_isdb.json, jira_isreq.json, pagerduty.json, oncall.ics}` — append-only, never pruned (FR-028).

---

## State transitions

- **RoleOverride**: `created` → (region-local midnight passes) → `expired` (no longer applied; weekly default resumes). Expired overrides are retained as history, not deleted.
- **Ticket status → group**: `To Do/Untriaged/Blocked` (To Do) → `In Progress/In Review` (WIP) → `Done` (Success). Distractor membership is orthogonal (driven by touch + non-assignment / different sprint), not a status.
- **Alert**: `acknowledged` → `resolved` (both states counted in their respective columns; total = ack + resolved).
- **BVG strict mode** (`ui_state`): `off` ⇄ `on`, toggled from the UI; affects only BVG ISReq coloring; control hidden when no engineer is BVG today.

---

## Validation rules (from requirements)

- Every roster engineer email resolves to a PagerDuty identity, else blocking startup error (FR-005a).
- A ticket is in pulse only via its own project's active sprint (FR-012).
- Success tickets render green regardless of role (FR-017).
- Cross-region combine deduplicates tickets by `ticket.id` and alerts by `alert.id` before summing; multi-region managers counted once (FR-024).
- Global-management engineers excluded from all counts-table totals and the alert-% denominator (FR-004); denominator = deduplicated AMER+APAC+EMEA totals (clarified).
- Day bucketing, effective-role day, and override expiry all use the engineer's/region's timezone (FR-022, FR-009, clarified).
- No persisted record is ever deleted (FR-028).
