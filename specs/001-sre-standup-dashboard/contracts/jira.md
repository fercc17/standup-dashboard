# Contract: Jira Cloud REST (consumed, read-only)

Base URL: `https://warthogs.atlassian.net`
Auth: HTTP Basic — username = `fernando.carrillo.castro@canonical.com`, password = token from `secrets/jira_token.txt`.
**All calls are GET (read-only). No endpoint here mutates Jira (FR-027).**

The dashboard depends only on the following read surface. Field lists are the minimum needed; exact response shapes are captured in raw snapshots.

### 1. Resolve active sprint per project ("pulse")

- `GET /rest/agile/1.0/board?projectKeyOrId={ISDB|ISReq}` → board id(s) for the project.
- `GET /rest/agile/1.0/board/{boardId}/sprint?state=active` → active sprint `{id, name, startDate, endDate, state}`.

**Consumed**: sprint `id`, `name`, `startDate`, `endDate`. Defines pulse window + counts-table day rows.

### 2. Tickets in the active sprint (assigned-work groups)

- `GET /rest/agile/1.0/sprint/{sprintId}/issue?fields=summary,status,priority,labels,assignee,sprint&expand=changelog`
  (paginate via `startAt`/`maxResults`).

**Consumed per issue**: `key`, `fields.summary` (title; `[PR/MP Review]` prefix detection), `fields.status.name` (group mapping), `fields.priority.name` (`Highest`), `fields.labels` (`ps5-blockers`), `fields.assignee.emailAddress`, sprint membership, `changelog`.

### 3. Candidate tickets touched during the pulse (Distractors + touch counts)

- `GET /rest/api/3/search?jql=project in (ISDB, ISReq) AND updated >= "{pulseStart}"&fields=summary,status,priority,labels,assignee&expand=changelog`
  (paginate).
- Per candidate issue, also read:
  - `GET /rest/api/3/issue/{key}/comment` → comment authors + timestamps.
  - `GET /rest/api/3/issue/{key}/worklog` → worklog authors + `started`.
  - Changelog (from `expand=changelog`) for status changes, assignment changes (`assignee` field), and link additions (`Link`/`IssueLink` items).

**Touch attribution (FR-014)** — an engineer "touched" an issue if, within the pulse window, they authored a comment, logged work, or appear as the actor of a changelog entry of type status / assignment / link. A touched, non-assigned (or different-sprint) issue → Distractor (FR-013).

### 4. Daily counts inputs

Derived from the issues fetched above plus targeted searches (all GET, read-only):

- **Open Highest ISReq (snapshot)**: `GET /rest/api/3/search?jql=project = ISReq AND priority = Highest AND statusCategory != Done`.
- **New Highest ISReq (24h)**: same with `AND created >= -24h` (relative to fetch).
- **Open `ps5-blockers` (snapshot)**: `... labels = ps5-blockers AND statusCategory != Done`.
- **New `ps5-blockers` (24h)**: `... labels = ps5-blockers AND created >= -24h`.
- **ISDB completed that day**: from changelog transitions into `Done` within each region-day bucket, or `jql=project = ISDB AND status changed to Done during (...)`.

### Read-only guarantee

The Jira client exposes only `get(...)` helpers; a unit test asserts no POST/PUT/DELETE/PATCH is ever issued. Rate/error handling: on non-2xx, mark `jira_ok = false` for the snapshot and fall back to last good data.
