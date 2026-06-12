# Contract: PagerDuty REST + iCal (consumed, read-only)

Auth: header `Authorization: Token token=<from secrets/pagerduty_token.txt>`, `Accept: application/vnd.pagerduty+json;version=2`.
**All calls are GET (read-only). Nothing here mutates PagerDuty (FR-027).**

### 1. Identity resolution (engineer ↔ PagerDuty user)

- `GET /users?query={email}` (or list users and index by email) → map each roster email to a PagerDuty user id.
- **Contract**: every roster engineer email MUST match a PagerDuty user. An unmatched engineer triggers a blocking setup error naming them (FR-005a).

### 2. Alerts acknowledged / resolved per member per day

- `GET /incidents?since={pulseStart}&until={now}&time_zone=UTC&statuses[]=acknowledged&statuses[]=resolved` (paginate via `offset`/`limit`).
- For attribution of who acknowledged/resolved and when, read incident log entries:
  - `GET /incidents/{id}/log_entries` → entries of type `acknowledge_log_entry` / `resolve_log_entry` with `agent` (the user) and `created_at`.

**Consumed**: incident `id` (dedup key), the acting user (→ engineer email), the action (`acknowledged`/`resolved`), and `created_at` (bucketed to a day per region timezone).

**Counts (FR-021)**: per region per day — alerts acknowledged by members, alerts resolved by members, total = ack + resolved, and region share = region total ÷ deduplicated AMER+APAC+EMEA total (clarified denominator). Alerts are deduplicated by incident `id` across regions before summing (FR-024).

### 3. Weekend on-call (iCal feed)

- `GET {url from secrets/pagerduty_ical_url.txt}` → an iCalendar document (PagerDuty schedule feed).
- Parse with `icalendar`; find the VEVENT covering the weekend; extract the on-call person (SUMMARY / ATTENDEE) and match to a roster engineer by name/email.

**Consumed (FR-025)**: the single weekend on-call engineer and the Sat/Sun span. All other engineers are treated as OFF Sat/Sun; on Monday the on-call engineer's Sat+Sun activity is combined.

### Read-only guarantee & failure handling

The PagerDuty client exposes only read helpers; a unit test asserts only GET is issued. On non-2xx or feed parse failure, mark `pagerduty_ok` / `ical_ok` = false for the snapshot and fall back to last good data with a visible failure indicator.
