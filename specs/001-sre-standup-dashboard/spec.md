# Feature Specification: IS SRE Standup Dashboard

**Feature Branch**: `001-sre-standup-dashboard`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "Build a single-user local web application called IS SRE Standup Dashboard. The purpose is to give an SRE manager full visibility of team activity before and during daily stand up, without manually switching between Jira, PagerDuty, and mental state tracking."

## Clarifications

### Session 2026-06-11

- Q: For the Project role, the prose ("touching ISReq is a yellow flag") contradicts the color matrix ("assigned ISReq is red"). Which wins? → A: The color matrix is authoritative — Project's assigned ISReq is **red**.
- Q: What is the "global" denominator for the region-alert-percentage column? → A: All alerts handled by the three modeled regions combined (AMER+APAC+EMEA), deduplicated by alert ID.
- Q: How are engineers matched across Jira and PagerDuty, and what happens on a mismatch? → A: Match by email (roster emails); an engineer with no matching PagerDuty identity is a hard setup error that blocks the dashboard until resolved.
- Q: Where do global managers (Kristofer, Alexandre Micouleau) appear in the UI? → A: As chips under a dedicated "Global" group header shown alongside any selected region, excluded from all counts-table totals.
- Q: When the viewer's local day differs from a region's day, which timezone drives an engineer's effective role and override expiry? → A: The engineer's region timezone, consistent with the per-region day and counts bucketing.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See whether each engineer is working on the right thing (Priority: P1)

Before a regional stand up, the manager opens the dashboard, selects a region, and immediately sees one chip per engineer showing that engineer's role for the day (color-coded), how many tickets they touched in the last 24 hours, and how many PagerDuty alerts they handled. Clicking a chip opens a detail panel that classifies the engineer's tickets into To Do, WIP, Success, and Distractors, with each ticket colored green/yellow/red according to whether the work matches the engineer's role that day.

**Why this priority**: This is the core value of the tool — replacing the manager's manual, error-prone mental cross-referencing of Jira and role expectations with an at-a-glance, role-aware view. Even with nothing else, this slice lets the manager run a stand up with full context.

**Independent Test**: With engineer roles set and data fetched for one region, verify each engineer appears as a chip with the correct role color, that clicking it reveals the four ticket groups, and that ticket colors follow the role-based color rules (e.g., a Project-role engineer's ISReq ticket shows red, their ISDB ticket shows green, completed tickets always show green).

**Acceptance Scenarios**:

1. **Given** a region is selected and data has been fetched, **When** the manager views the page, **Then** every engineer in that region (including the region manager) is shown as a chip with their name, today's role in the role's color, count of tickets touched in the last 24 hours, and PagerDuty alerts in the last 24 hours shown as acknowledged/resolved.
2. **Given** an engineer chip is visible, **When** the manager clicks it, **Then** a detail panel opens showing four groups — To Do, WIP, Success, Distractors — populated from the current pulse.
3. **Given** an engineer has the Project role today, **When** their detail panel is shown, **Then** assigned ISDB tickets are green, assigned ISReq tickets are red, and any non-assigned touched ticket is red.
4. **Given** an engineer has the GEN role today, **When** their detail panel is shown, **Then** assigned ISReq tickets that are Highest priority or carry the `ps5-blockers` label are green, all other assigned ISReq tickets are red, assigned ISDB tickets are red, and non-assigned touches are red.
5. **Given** any engineer and any role, **When** a ticket is in the Success group (Done), **Then** it is shown green regardless of role.
6. **Given** multiple chips, **When** the manager clicks several of them, **Then** multiple detail panels can be open at the same time.

---

### User Story 2 - Set and adjust engineer roles (Priority: P1)

The manager opens a role schedule modal showing a Monday–Friday grid plus a Weekend column for every engineer, each cell a dropdown of the five roles (PVG, BVG, GEN, Project, OFF). The manager sets the weekly default per engineer. A separate per-day override row lets the manager change a single engineer's role for today only, without altering the weekly default; the override expires at midnight local viewer time. When at least one engineer is BVG today, a strict-mode toggle is available that tightens what counts as expected BVG work.

**Why this priority**: Role assignment is the input that makes the entire color-coding and "right work" judgment in User Story 1 meaningful. Without it the dashboard cannot tell expected work from waste. It is therefore co-critical (P1) with the activity view.

**Independent Test**: Open the schedule modal, set a weekly role for an engineer, confirm it persists across refreshes; apply a today-only override and confirm the weekly default is unchanged and the override disappears after local midnight; toggle BVG strict mode and confirm the change takes effect without editing any config file.

**Acceptance Scenarios**:

1. **Given** the schedule modal is open, **When** the manager selects a role for an engineer on a weekday, **Then** that weekly default persists and is reflected in the engineer's chip on that day.
2. **Given** a weekly default is set, **When** the manager sets a today-only override for an engineer, **Then** the engineer's effective role for today reflects the override while the weekly default remains unchanged.
3. **Given** a today-only override is active, **When** the local viewer clock passes midnight, **Then** the override is no longer applied and the weekly default resumes.
4. **Given** at least one engineer has the BVG role today, **When** the manager views the top bar, **Then** a BVG strict-mode toggle is visible; **When** no engineer is BVG today, **Then** the toggle is hidden.
5. **Given** BVG strict mode is ON, **When** evaluating a BVG engineer's assigned ISReq tickets, **Then** only Highest-priority or `ps5-blockers` tickets are green and all other ISReq tickets are yellow; **When** strict mode is OFF, **Then** all assigned ISReq tickets are green.
6. **Given** the strict-mode toggle is changed, **When** the change is made, **Then** it takes effect in the UI without editing any configuration file.

---

### User Story 3 - Read sprint throughput and alert load for the pulse (Priority: P2)

Below the region buttons the manager sees a counts table with one row per calendar day of the current pulse. Columns summarize open and newly-arrived Highest ISReq tickets, open and new `ps5-blockers` tickets, ISDB tickets completed that day, PagerDuty alerts acknowledged and resolved by region members, total alerts, and the region's share of all global alerts that day. Monday shows a single combined Saturday+Sunday weekend row.

**Why this priority**: This gives the manager trend and load context for the sprint that complements the per-engineer view. It is valuable but the stand up can still run without it, so it ranks below the activity and role slices.

**Independent Test**: With a pulse active and data fetched for one region, verify the table has one row per pulse day, that each column shows the defined metric for that day's region timezone, and that the Monday row combines Saturday and Sunday data.

**Acceptance Scenarios**:

1. **Given** a region is selected and a pulse is active, **When** the manager views the counts table, **Then** there is one row per calendar day of the current pulse plus a combined weekend row shown on Monday.
2. **Given** a day's row, **When** the manager reads it, **Then** it shows: open Highest ISReq (snapshot at fetch), new Highest ISReq in last 24h, ISDB completed that day, open `ps5-blockers` (snapshot at fetch), new `ps5-blockers` in last 24h, alerts acknowledged, alerts resolved, total alerts, and region alerts as a percentage of all global alerts that day.
3. **Given** the AMER region is selected, **When** days are bucketed, **Then** day boundaries use the America/Mexico_City timezone; for APAC, Australia/Sydney; for EMEA, Europe/Paris.

---

### User Story 4 - Combine multiple regions without double-counting (Priority: P2)

The manager selects more than one region at once. Engineer chips are grouped under a per-region header. The counts table shows combined figures with tickets deduplicated by ticket ID and alerts deduplicated by alert ID before summing, each region still bucketed by its own timezone, and combined numbers compared against the global team total. A manager who owns more than one selected region (e.g., Fernando owns AMER and APAC) appears once under each region but is never double-counted in the combined totals.

**Why this priority**: The manager runs AMER and APAC and oversees EMEA, so cross-region views are a real workflow, but single-region operation already delivers the core value, so this is P2.

**Independent Test**: Select two regions sharing a manager, verify the manager's chip appears under both region headers, and verify a ticket or alert that would appear in both regions is counted only once in the combined counts table.

**Acceptance Scenarios**:

1. **Given** two or more regions are selected, **When** chips are displayed, **Then** they are grouped under a header per region.
2. **Given** a manager owns two selected regions, **When** chips are displayed, **Then** the manager appears once under each owned region.
3. **Given** two or more regions are selected, **When** the counts table is computed, **Then** tickets are deduplicated by ticket ID and alerts by alert ID before summing, and a multi-region manager's tickets and alerts are counted only once.
4. **Given** combined regions, **When** the alert percentage is computed, **Then** it compares the deduplicated combined region alerts against the global team total.

---

### User Story 5 - Account for weekend on-call coverage (Priority: P3)

Weekend on-call is determined from a PagerDuty iCal feed: exactly one engineer covers the whole weekend regardless of region, and every other engineer is implicitly OFF on Saturday and Sunday. On Monday the dashboard presents the on-call engineer's combined Saturday+Sunday activity.

**Why this priority**: It is a correctness refinement for Monday stand ups and weekend attribution, important but only relevant on Mondays and dependent on the iCal feed, so it ranks below the core weekday workflow.

**Independent Test**: With a PagerDuty iCal feed naming a weekend on-call engineer, verify that on Monday only that engineer shows weekend activity, all others are treated as OFF for Saturday and Sunday, and the engineer's Saturday and Sunday data is shown combined.

**Acceptance Scenarios**:

1. **Given** a weekend with an on-call engineer named in the iCal feed, **When** the dashboard evaluates Saturday and Sunday, **Then** that engineer is the only one not OFF and all other engineers are treated as OFF.
2. **Given** it is Monday, **When** the manager views the on-call engineer, **Then** their Saturday and Sunday activity is presented as a single combined weekend view.

---

### User Story 6 - Retain history for future trend analysis (Priority: P3)

Every fetch of Jira and PagerDuty data is stored locally and timestamped, and historical data is never deleted, so that later pulses can be compared and trends analyzed. The tool never writes back to Jira or PagerDuty.

**Why this priority**: Retention enables future analysis but provides no immediate stand-up value, so it is the lowest priority while still being a firm requirement to capture from day one.

**Independent Test**: Perform two fetches at different times, verify both are persisted with timestamps and that the earlier data is still present after the later fetch, and verify no write requests are issued to Jira or PagerDuty.

**Acceptance Scenarios**:

1. **Given** a fetch completes, **When** data is stored, **Then** it is persisted locally with a fetch timestamp.
2. **Given** a later fetch occurs, **When** it completes, **Then** previously stored data remains intact and is not deleted or overwritten in a way that loses history.
3. **Given** any dashboard operation, **When** it runs, **Then** no create/update/delete request is sent to Jira or PagerDuty.

---

### Edge Cases

- **No credentials present**: When a required secret file is missing or empty, the dashboard surfaces a clear setup message pointing to the expected `secrets/` file rather than failing silently.
- **Fetch failure / partial source outage**: When Jira or PagerDuty is unreachable, the dashboard shows the last successfully fetched data with its timestamp and indicates that the latest refresh failed.
- **No active pulse**: When a project has no currently active sprint, the dashboard indicates there is no active pulse rather than showing an empty or misleading table.
- **Engineer with no activity**: An engineer who touched no tickets and handled no alerts still appears as a chip with zero counts.
- **Ticket touched by multiple engineers**: A ticket touched by several engineers appears as a Distractor for each non-assignee who touched it, and once for its assignee in the appropriate assigned group.
- **Ticket from a different sprint touched during the pulse**: It appears only as a Distractor, never in To Do/WIP/Success.
- **Multi-region day mismatch**: When selected regions are on different calendar days (timezone rollover), each region's rows are bucketed by its own timezone before combining.
- **Override and weekend overlap**: A today-only override on a weekend interacts with weekend on-call rules; the on-call engineer's effective role is still resolved for display.
- **BVG strict toggle with no BVG engineer**: The toggle is hidden and strict-mode state has no visible effect when no engineer is BVG that day.
- **`[PR/MP Review]` ISReq tickets**: ISReq tickets whose title begins with `[PR/MP Review]` are identified as the BVG review ticket type and classified accordingly.
- **Global managers**: Engineers in global management (not assigned to a regional squad) are visible but not counted within any region's squad totals.

## Requirements *(mandatory)*

### Functional Requirements

#### Regions, team, and selection

- **FR-001**: System MUST present three region selectors — AMER, APAC, EMEA — at the top of the page, and MUST allow more than one region to be selected simultaneously.
- **FR-002**: System MUST show, for each selected region, the current local day computed in that region's timezone (AMER → America/Mexico_City, APAC → Australia/Sydney, EMEA → Europe/Paris).
- **FR-003**: System MUST associate each engineer with their region(s) and display the region manager among that region's engineers.
- **FR-004**: System MUST display engineers who belong to global management as chips under a dedicated "Global" group header, shown alongside any selected region, and MUST exclude their tickets and alerts from every region's counts-table totals (including the global team total used for the alert percentage).
- **FR-005**: When multiple regions are selected, System MUST group engineer chips under a per-region header, and MUST show a manager who owns multiple selected regions once under each owned region.
- **FR-005a**: System MUST match each engineer's Jira and PagerDuty identities by the roster email address. If any engineer has no matching PagerDuty identity, System MUST treat this as a setup error and block the dashboard, naming the unmatched engineer(s), until the mismatch is resolved.

#### Roles and schedule

- **FR-006**: System MUST support exactly five roles — PVG, BVG, GEN, Project, OFF — assignable per engineer.
- **FR-007**: System MUST let the manager set a weekly default role per engineer for each weekday and a Weekend column, via a schedule modal with a per-cell role dropdown, and MUST persist weekly changes.
- **FR-008**: System MUST support a today-only role override per engineer that does not modify the weekly default and that automatically expires at midnight in the engineer's region timezone.
- **FR-009**: System MUST resolve each engineer's effective role for a given day using the engineer's region timezone to determine "today" and the current weekday, as: the today-only override if present, otherwise the weekly default for that weekday, otherwise the weekend rule for Saturday/Sunday. The same region-timezone "today" governs which calendar day is used for an engineer's chip role.
- **FR-010**: System MUST provide a BVG strict-mode toggle in the top bar that is visible only when at least one engineer has the BVG role for the current day, and MUST let the manager change it from the UI without editing configuration files.
- **FR-011**: System MUST treat the manager as the only user; no role or setting in the tool is editable by anyone other than the single operator.

#### Pulse and ticket classification

- **FR-012**: System MUST define the pulse as the currently active two-week sprint, evaluated per project (ISDB and ISReq), and MUST treat a ticket as "in pulse" when it belongs to the active sprint of its own project.
- **FR-013**: System MUST classify each engineer's tickets into four groups: To Do (assigned, in pulse, status To Do/Untriaged/Blocked), WIP (assigned, in pulse, status In Progress/In Review), Success (assigned, in pulse, status Done), and Distractors (touched during the pulse but not assigned to the engineer in this sprint, or belonging to a different sprint).
- **FR-014**: System MUST consider a ticket "touched" by an engineer during the pulse if the engineer performed any of: status change, comment, assignment change, worklog entry, or link addition.
- **FR-015**: System MUST identify ISReq tickets whose title begins with `[PR/MP Review]` as the BVG review ticket type.

#### Color coding

- **FR-016**: System MUST color each ticket green, yellow, or red based on the engineer's effective role and the ticket's project, per the following rules:
  - **PVG**: assigned ISReq → red; assigned ISDB → red; non-assigned touch → green.
  - **BVG**: assigned ISReq → green (in strict mode: green only if Highest priority or `ps5-blockers`, otherwise yellow); assigned ISDB → red; non-assigned touch → green.
  - **GEN**: assigned ISReq → green if Highest priority or `ps5-blockers`, otherwise red; assigned ISDB → red; non-assigned touch → red.
  - **Project**: assigned ISReq → red; assigned ISDB → green; non-assigned touch → red.
  - **OFF**: every ticket → red.
- **FR-017**: System MUST always color the Success group green regardless of the engineer's role.

#### Engineer chips and detail panels

- **FR-018**: System MUST render one chip per engineer in the selected region(s) showing the engineer's name, today's effective role (in the role's color), count of tickets touched in the last 24 hours, and PagerDuty alerts in the last 24 hours shown as acknowledged/resolved.
- **FR-019**: System MUST open a detail panel when a chip is clicked, showing the four ticket groups with color coding applied, and MUST allow multiple detail panels to be open simultaneously.

#### Counts table

- **FR-020**: System MUST display a counts table with one row per calendar day of the current pulse for the selected region(s).
- **FR-021**: Each counts row MUST include: open Highest-priority ISReq tickets (snapshot at fetch time), new Highest ISReq tickets in the last 24 hours, ISDB tickets completed that day, open `ps5-blockers` tickets (snapshot at fetch time), new `ps5-blockers` tickets in the last 24 hours, PagerDuty alerts acknowledged by region members that day, alerts resolved by region members that day, total alerts (acknowledged + resolved), and the region's alerts as a percentage of the global team total for that day — where the global team total is all alerts handled by the three modeled regions combined (AMER+APAC+EMEA), deduplicated by alert ID.
- **FR-022**: System MUST bucket each day using the timezone of the region being evaluated, and when multiple regions are selected MUST evaluate each region against its own timezone before combining. (Cross-region deduplication of tickets and alerts is governed by FR-024.)
- **FR-023**: System MUST present Saturday and Sunday as a single combined weekend row shown on Monday.
- **FR-024**: When multiple regions are selected, System MUST deduplicate tickets by ticket ID and alerts by alert ID before summing combined counts, ensuring a multi-region manager's tickets and alerts are never double-counted, and MUST compare combined region totals against the global team total (the deduplicated sum across all three modeled regions AMER+APAC+EMEA for that day).

#### Weekend on-call

- **FR-025**: System MUST determine the single weekend on-call engineer from the PagerDuty iCal feed, treat all other engineers as implicitly OFF on Saturday and Sunday regardless of region, and present the on-call engineer's combined Saturday+Sunday activity on Monday.

#### Refresh and data flow

- **FR-026**: System MUST provide a manual refresh control that fetches current data from Jira and PagerDuty and MUST display the timestamp of the last successful fetch.
- **FR-027**: System MUST be read-only toward external sources: it MUST NOT create, update, or delete anything in Jira or PagerDuty.

#### Storage and history

- **FR-028**: System MUST store all fetched data locally, timestamp each fetch, and never delete historical data, so that future trend analysis across pulses is possible.

#### Credentials

- **FR-029**: System MUST read credentials only from individual plain-text files under a `secrets/` directory — `secrets/jira_token.txt`, `secrets/pagerduty_token.txt`, `secrets/pagerduty_ical_url.txt` — and MUST NOT store credentials in code or in committed files.
- **FR-030**: System MUST keep the `secrets/` directory out of version control, and MUST provide committed example placeholder files that show the required structure to anyone setting up the tool.

#### Presentation

- **FR-031**: System MUST present the interface as a single vertical page optimized for half-screen width, targeting a usable layout at ~720px wide (a half-screen window on a 1440px display) without horizontal scrolling, and remaining functional from ~600px to ~960px.
- **FR-032**: System MUST place, in the top bar, the region selectors, the per-region current local day, the manual refresh control with last-fetch timestamp, and (conditionally) the BVG strict-mode toggle.

### Key Entities *(include if data involved)*

- **Region**: A geographic squad grouping (AMER, APAC, EMEA) with an associated timezone, a manager, and a set of engineers.
- **Engineer**: A team member identified by name and email, belonging to one or more regions (managers may span regions) or to global management; has a resolved effective role per day.
- **Role**: One of PVG, BVG, GEN, Project, OFF, defining expected work and color-coding behavior; BVG additionally has a strict-mode behavior.
- **Role Schedule**: The weekly default role per engineer per weekday plus a Weekend value; the source of an engineer's default effective role.
- **Role Override**: A today-only role assignment for one engineer that supersedes the weekly default and expires at local midnight.
- **Pulse / Sprint**: The currently active two-week sprint of a project; the time window that scopes ticket classification and counts.
- **Project**: A Jira project — ISDB (roadmap work) or ISReq (customer requests).
- **Ticket**: A unit of work in a project with an ID, title, status, priority, labels (e.g., `ps5-blockers`), assignee, and sprint membership; classified into To Do / WIP / Success / Distractors per engineer.
- **Touch Event**: An engineer action on a ticket during the pulse (status change, comment, assignment change, worklog entry, or link addition) used to determine "touched."
- **Alert**: A PagerDuty incident handled by a region member, with an ID and an acknowledged/resolved state, attributed to a day and region.
- **Weekend On-Call Assignment**: The single engineer covering a weekend, sourced from the PagerDuty iCal feed.
- **Fetch Snapshot**: A timestamped, locally stored capture of fetched Jira and PagerDuty data, retained permanently for history.
- **Credential File**: A plain-text secret under `secrets/` (Jira token, PagerDuty token, Jira iCal URL), never committed, with a committed placeholder example.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The manager can determine, for any selected region, which engineers are working on the wrong thing for their role in under 30 seconds without opening Jira or PagerDuty.
- **SC-002**: Every engineer in a selected region is represented by exactly one chip per region they appear under, with no engineer missing and no duplicate within a region.
- **SC-003**: 100% of tickets shown in detail panels are colored according to the role-and-project rules, and completed (Success) tickets are green in 100% of cases regardless of role.
- **SC-004**: When two or more regions are combined, no ticket and no alert is counted more than once, verified by comparing combined totals against per-region totals for shared items.
- **SC-005**: A role change made in the schedule modal is reflected in the affected engineer's chip on the next view without editing any file, and a today-only override disappears automatically after local midnight.
- **SC-006**: The counts table shows one row per pulse day with the Monday row combining Saturday and Sunday, and each day is bucketed in the correct regional timezone.
- **SC-007**: After repeated fetches over time, no previously fetched data is lost, and the tool issues zero write operations to Jira or PagerDuty.
- **SC-008**: A new operator can set up the tool using only the committed placeholder files as a guide, and no real credential ever appears in version control.

## Assumptions

- The application is single-user and runs locally; it requires no multi-user authentication or authorization layer beyond the operator's own machine.
- "Last 24 hours" and "new in the last 24 hours" are measured relative to the most recent successful fetch time.
- "Open" Highest-ISReq and `ps5-blockers` counts are point-in-time snapshots taken at fetch time, as stated in the column definitions.
- Both Jira projects (ISDB and ISReq) run two-week sprints aligned to the same calendar boundaries, so a single "pulse" concept applies to both.
- Refresh is manual only; there is no automatic background polling requirement.
- Engineer identity is matched across Jira and PagerDuty by the email addresses provided in the team roster; a missing PagerDuty match is a blocking setup error (see FR-005a), not a silent zero.
- The Jira base URL is `https://warthogs.atlassian.net`, projects are ISDB and ISReq, and the Jira API account email is fernando.carrillo.castro@canonical.com (configuration inputs, not behavior).
- The five roles, their expected-work rules, and the color-coding matrix are fixed as specified and are not user-configurable beyond per-day/per-week role assignment and the BVG strict-mode toggle.
- A "day" for role scheduling, effective-role resolution, and override expiry is evaluated in the engineer's region timezone, consistent with the per-region day display and the counts-table day bucketing.
- Historical retention has no defined cap in this version; storage growth is acceptable for the single-user local scope.

## Dependencies

- Read access to Jira Cloud for the ISDB and ISReq projects via an API token.
- Read access to PagerDuty for alert/incident data via an API token.
- A PagerDuty iCal feed URL that identifies the weekend on-call engineer.
