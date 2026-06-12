# Quickstart & Validation Guide: IS SRE Standup Dashboard

This guide takes a fresh checkout to a running, validated dashboard. It is a run/validation guide — implementation details live in `data-model.md`, `contracts/`, and (later) `tasks.md`.

## Prerequisites

- Python 3.12 and `uv` installed.
- Read-only credentials: a Jira Cloud API token, a PagerDuty API token, and a PagerDuty schedule iCal URL.
- Network access to `https://warthogs.atlassian.net` and PagerDuty.

## 1. Install

```bash
uv sync                      # create venv + install deps from pyproject.toml
```

## 2. Provide credentials (never committed)

```bash
cp -r secrets.example/. secrets/      # copy placeholder files into the gitignored secrets/ dir
# then edit each file to contain ONLY the real value:
#   secrets/jira_token.txt        -> Jira API token
#   secrets/pagerduty_token.txt   -> PagerDuty API token
#   secrets/pagerduty_ical_url.txt     -> PagerDuty weekend on-call iCal URL
```

`secrets/` and `data/` are gitignored. Confirm no secret is tracked:

```bash
git check-ignore secrets/jira_token.txt data/dashboard.db   # both should print (i.e. ignored)
git status --porcelain | grep -E 'secrets/(jira|pagerduty)_' && echo "LEAK!" || echo "clean"
```

## 3. Run

```bash
uv run python -m standup_dashboard        # starts uvicorn on http://localhost:8765
```

Open `http://localhost:8765`. On first run with valid secrets the dashboard loads; click **Refresh** to perform the first fetch.

## 4. End-to-end validation scenarios

Each maps to spec acceptance criteria / success criteria. ✅ = expected outcome.

### S1 — Role-aware activity (US1, FR-016/017/018/019)
1. Select **AMER**. Click **Refresh**.
2. ✅ Every AMER engineer (incl. manager Fernando) shows one chip with name, today's role in its color, tickets-touched-24h, and alerts-24h (ack/resolved).
3. Click an engineer with the **Project** role → ✅ detail panel shows assigned ISDB green, assigned ISReq red, non-assigned touches red; any Done ticket is green.
4. Click a second chip → ✅ both panels stay open.

### S2 — Roles & strict mode (US2, FR-007/008/009/010)
1. Open **Schedule** → set an engineer's Monday default to `BVG`, save → ✅ persists across a page reload.
2. Set a **today-only override** to `OFF` for another engineer → ✅ chip shows OFF today; weekly default unchanged in the grid.
3. ✅ BVG **strict-mode** toggle is visible (a BVG engineer exists today). Toggle it → a BVG engineer's non-Highest/non-`ps5-blockers` ISReq flips green↔yellow.
4. (Time check) After region-local midnight → ✅ the override no longer applies.

### S3 — Counts table (US3, FR-020/021/022/023)
1. With AMER selected, inspect the counts table → ✅ one row per pulse day; the **Monday** row combines Saturday+Sunday.
2. ✅ Columns present: open Highest ISReq, new Highest ISReq (24h), ISDB completed that day, open `ps5-blockers`, new `ps5-blockers` (24h), alerts ack, alerts resolved, total, region %.
3. ✅ Days are bucketed in America/Mexico_City for AMER.

### S4 — Multi-region dedup (US4, FR-005/024)
1. Select **AMER + APAC** → ✅ chips grouped under per-region headers; Fernando appears once under each.
2. ✅ A ticket/alert shared across regions is counted once in the combined table; region % uses the deduplicated AMER+APAC+EMEA denominator.

### S5 — Weekend on-call (US5, FR-025)
1. On a Monday, with an iCal on-call set → ✅ only the on-call engineer shows weekend activity; others are OFF for Sat/Sun; the on-call engineer's Sat+Sun is shown combined.

### S6 — History & read-only (US6, FR-027/028)
1. Click **Refresh** twice at different times → ✅ two timestamped snapshots exist under `data/snapshots/`; the earlier one is still present.
2. ✅ No write request is sent to Jira/PagerDuty (guard test green; see below).

### S7 — Setup safety (FR-005a/029/030)
1. Empty `secrets/jira_token.txt` and reload → ✅ a blocking setup page names the missing file (no dashboard).
2. Add a roster engineer whose email has no PagerDuty match → ✅ blocking setup page names that engineer.

## 5. Automated checks

```bash
uv run pytest -q                  # unit (color matrix, role/tz/dedup, touch attribution) + integration (mocked HTTP)
uv run pytest -q -k read_only     # guard: external clients issue only GET requests
uv run ruff check .
```

✅ **Definition of done for this feature**: all S1–S7 scenarios pass manually and `pytest`/`ruff` are green.
