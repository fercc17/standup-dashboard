# Contract: Internal Web Routes (local UI ↔ FastAPI)

The UI is server-rendered (Jinja2 + HTMX). Routes return either a full HTML page or an HTML fragment (for HTMX swaps). All routes are local, single-user, unauthenticated (bound to localhost). No route mutates Jira or PagerDuty.

Base URL: `http://localhost:<port>`

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/` | Full dashboard page | query `regions` (repeatable: `AMER`/`APAC`/`EMEA`; default last-used or AMER) | Full HTML: top bar, counts table, chip grid, Global group |
| POST | `/refresh` | Trigger a manual fetch from Jira + PagerDuty + iCal; persist a new snapshot | form `regions[]` | HTML fragment: updated counts table + chips + last-fetch timestamp; on partial failure, last good data + a failure banner |
| GET | `/chip/{engineer_email}/detail` | Open an engineer detail panel | path `engineer_email`; query `regions[]` | HTML fragment: four ticket groups (To Do / WIP / Success / Distractors) with per-ticket color |
| POST | `/toggle/strict` | Toggle BVG strict mode | form `value` (`on`/`off`) | HTML fragment: re-rendered chips/panels reflecting new coloring; 404/hidden if no BVG engineer today |
| GET | `/schedule` | Open the role schedule modal | none | HTML fragment: Mon–Fri + Weekend grid of role dropdowns per engineer + today-override row |
| POST | `/schedule/weekly` | Persist a weekly default role | form `engineer_email`, `weekday`, `role` | HTML fragment: updated cell; 200 on success |
| POST | `/schedule/override` | Set a today-only override (expires at region-local midnight) | form `engineer_email`, `role` | HTML fragment: updated override row + affected chip |

### Behavioral contract notes

- **Region selection**: `regions` is multi-valued; selecting >1 groups chips under per-region headers and combines the counts table with dedup by ticket id / alert id (FR-005, FR-024).
- **Last-fetch timestamp**: every page/fragment that shows data also shows the `fetched_at` of the snapshot it rendered (FR-026).
- **Read-only**: there is no route that writes to Jira/PagerDuty; `/refresh` only reads remote and writes local storage (FR-027).
- **Partial outage**: if a source fails during `/refresh`, respond 200 with the last successful data and a visible "latest refresh failed" indicator (Edge Cases).
- **Setup errors**: if a secret file is missing/empty, or a roster engineer has no PagerDuty match, the app serves a blocking setup page instead of the dashboard (FR-005a, FR-029).
- **Multiple panels**: `/chip/.../detail` fragments are additive in the DOM (HTMX target appends), so several panels stay open at once (FR-019).

### Error responses

| Condition | Status | Body |
|---|---|---|
| Missing/empty secret file | 200 (setup page) | Names the expected `secrets/<file>.txt` |
| Roster engineer unmatched in PagerDuty | 200 (setup page) | Names the unmatched engineer(s) |
| Unknown region value | 400 | Plain text error |
| Strict toggle when no BVG today | 404 | Control is not rendered in this state |
