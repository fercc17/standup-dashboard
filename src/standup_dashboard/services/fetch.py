"""Refresh orchestration: fan-out fetch → persist SQLite + raw snapshot (FR-026) — T025.

Reads Jira (pulses, sprint issues, touched candidates, comments, worklogs) and
PagerDuty (incidents + log entries) concurrently, derives tickets / touches /
alerts, and writes one append-only fetch layer plus full-fidelity raw JSON.
Per-source success flags drive partial-outage messaging and last-good fallback
(US6). Strictly read-only toward both systems (FR-027).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import config
from ..clients import github as gh_mod
from ..clients import ical as ical_mod
from ..clients import jira as jira_mod
from ..clients import pagerduty as pd_mod
from ..domain.models import (
    Alert,
    AlertState,
    GitHubPRStats,
    Pulse,
    Ticket,
    TouchEvent,
    WeekendOnCall,
)
from ..settings import Secrets
from ..storage.db import Database
from ..storage.snapshots import SnapshotWriter
from .oncall import resolve_oncall
from .pulse import current_pulse, parse_jira_dt, previous_pulse, resolve_pulses
from .touches import extract_touches, parse_ticket

logger = logging.getLogger("standup_dashboard.fetch")

@dataclass
class JiraResult:
    ok: bool = True
    pulses: list[Pulse] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)
    touches: list[TouchEvent] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyResult:
    ok: bool = True
    alerts: list[Alert] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ICalResult:
    ok: bool = True
    oncall: WeekendOnCall | None = None          # current / just-passed weekend
    next_oncall: WeekendOnCall | None = None      # the upcoming weekend (#weekend-next)
    raw: str | None = None


@dataclass
class GitHubResult:
    ok: bool = True
    pr_stats: dict[str, GitHubPRStats] = field(default_factory=dict)  # email → pulse PR stats


async def _fetch_jira(
    secrets: Secrets, now: datetime, window_start: datetime, roster: set[str]
) -> JiraResult:
    res = JiraResult()
    try:
        async with jira_mod.make_async_client(secrets.jira_token) as hc:
            jira = jira_mod.JiraClient(hc)
            res.pulses = await resolve_pulses(jira, config.PROJECT_KEYS)

            issues_by_key: dict[str, dict[str, Any]] = {}
            # A board can run several concurrent active sprints (e.g. the ISDB
            # board carries the shared cross-team sprint plus ISDB's own), so
            # fetch issues from EVERY active sprint across the projects' boards —
            # not just each project's primary one — or we miss sprint tickets that
            # weren't updated within the candidate-search window.
            seen_sprints: set[int] = set()
            for key in config.PROJECT_KEYS:
                for sprint in await jira.active_sprints(key):
                    sid = int(sprint["id"])
                    if sid in seen_sprints:
                        continue
                    seen_sprints.add(sid)
                    sprint_issues = await jira.sprint_issues(sid)
                    res.raw[f"jira_sprint_{sid}.json"] = sprint_issues
                    for issue in sprint_issues:
                        issues_by_key[issue["key"]] = issue

            # Best-effort candidate search (for Distractors). A failure here must
            # not discard the sprint issues we already have.
            jql = (
                f"project in ({', '.join(config.PROJECT_KEYS)}) "
                f'AND updated >= "{window_start.strftime("%Y-%m-%d %H:%M")}"'
            )
            try:
                candidates = await jira.search(jql)
                res.raw["jira_search.json"] = candidates
                for issue in candidates:
                    issues_by_key.setdefault(issue["key"], issue)
            except Exception:  # noqa: BLE001
                logger.exception("Jira candidate search failed; using sprint issues only")

            # Previous-pulse tickets (created or resolved in the prior pulse) so
            # the counts table can show a previous-pulse comparison (#80). Jira
            # retains these even after they roll out of the active sprint.
            _, prev_start, prev_end = previous_pulse(now.date())
            prev_jql = (
                f"project in ({', '.join(config.PROJECT_KEYS)}) AND ("
                f'(created >= "{prev_start}" AND created < "{prev_end}") OR '
                f'(resolved >= "{prev_start}" AND resolved < "{prev_end}"))'
            )
            try:
                prev_issues = await jira.search(prev_jql)
                res.raw["jira_prev_pulse.json"] = prev_issues
                for issue in prev_issues:
                    issues_by_key.setdefault(issue["key"], issue)
            except Exception:  # noqa: BLE001
                logger.exception("Jira previous-pulse search failed")

            res.tickets = [parse_ticket(issue) for issue in issues_by_key.values()]

            # Fetch comments + worklogs per issue concurrently (bounded).
            sem = asyncio.Semaphore(10)

            async def _touches_for(key: str, issue: dict[str, Any]) -> list[TouchEvent]:
                # Skip the extra comment/worklog calls for issues not updated in
                # the window — they can't have touches we'd count (big speedup).
                updated = parse_jira_dt((issue.get("fields") or {}).get("updated"))
                if updated is not None and updated < window_start:
                    comments: list[dict[str, Any]] = []
                    worklogs: list[dict[str, Any]] = []
                else:
                    async with sem:
                        comments = await jira.comments(key)
                        worklogs = await jira.worklogs(key)
                return extract_touches(
                    issue,
                    comments=comments,
                    worklogs=worklogs,
                    window_start=window_start,
                    window_end=now,
                    roster_emails=roster,
                )

            touch_lists = await asyncio.gather(
                *(_touches_for(k, i) for k, i in issues_by_key.items())
            )
            for touches in touch_lists:
                res.touches.extend(touches)
    except Exception:  # noqa: BLE001 — any failure marks the source down (US6)
        logger.exception("Jira fetch failed")
        res.ok = False
    return res


def _alerts_from_logs(
    incident_id: str,
    log_entries: list[dict[str, Any]],
    id_to_email: dict[str, str],
    roster: set[str],
    title: str | None = None,
    url: str | None = None,
    number: int | None = None,
) -> list[Alert]:
    out: list[Alert] = []
    ackers: dict[str, datetime] = {}          # roster acker → earliest ack time
    roster_resolved = False                    # resolved by a roster user?
    auto_resolve_at: datetime | None = None    # earliest resolve with no roster agent
    for entry in log_entries:
        etype = entry.get("type", "")
        at = parse_jira_dt(entry.get("created_at"))
        if at is None:
            continue
        if etype == "trigger_log_entry":
            # The trigger has no engineer agent; capture only the fire time so MTTA
            # (trigger→ack) is computable. Handler-less, so it never affects the
            # ack/resolve counts, which filter by member handler.
            out.append(Alert(id=incident_id, handler_email="", state=AlertState.TRIGGERED,
                             at=at, title=title, url=url, number=number))
            continue
        if etype not in ("acknowledge_log_entry", "resolve_log_entry"):
            continue
        agent = entry.get("agent") or {}
        email = id_to_email.get(agent.get("id", ""))
        roster_member = email if (email and email in roster) else None
        if etype == "acknowledge_log_entry":
            if roster_member:
                out.append(Alert(id=incident_id, handler_email=roster_member,
                                 state=AlertState.ACKNOWLEDGED, at=at,
                                 title=title, url=url, number=number))
                if roster_member not in ackers or at < ackers[roster_member]:
                    ackers[roster_member] = at
        elif roster_member:  # resolve by a roster user — credit the resolver
            out.append(Alert(id=incident_id, handler_email=roster_member,
                             state=AlertState.RESOLVED, at=at,
                             title=title, url=url, number=number))
            roster_resolved = True
        elif auto_resolve_at is None or at < auto_resolve_at:
            # Resolve with no roster agent: an integration/auto-resolve (the common
            # Prometheus "FIRING" case, agent = events_api_v2_inbound_integration)
            # or a non-roster user. Recorded to attribute to the acker(s) below.
            auto_resolve_at = at
    # An incident resolved without a roster resolver still has to leave the acker's
    # ACK bucket and count as resolved — otherwise an auto-resolved alert shows ACK
    # forever (#stale-ack). Attribute the resolve to each roster acker at its time.
    if auto_resolve_at is not None and not roster_resolved:
        for email, acked_at in ackers.items():
            out.append(Alert(id=incident_id, handler_email=email,
                             state=AlertState.RESOLVED, at=max(auto_resolve_at, acked_at),
                             title=title, url=url, number=number))
    return out


async def _fetch_pagerduty(
    secrets: Secrets, now: datetime, since: datetime, roster: set[str],
    recheck_ids: frozenset[str] = frozenset(),
) -> PagerDutyResult:
    res = PagerDutyResult()
    try:
        async with pd_mod.make_async_client(secrets.pagerduty_token) as hc:
            pd = pd_mod.PagerDutyClient(hc)
            users = await pd.list_users()
            id_to_email = {u["id"]: u.get("email", "") for u in users}
            # Hard floor: never request incidents from before PAGERDUTY_MIN_SINCE
            # (2026-06-08). With incremental fetches this only binds on a cold
            # start; afterwards ``since`` is the last successful PagerDuty fetch.
            since = max(since, config.PAGERDUTY_MIN_SINCE)
            # Scope to the roster's PagerDuty team(s) so we don't pull the whole org.
            incidents = await pd.incidents(since, now, team_ids=config.PAGERDUTY_TEAM_IDS)
            res.raw["pagerduty_incidents.json"] = incidents
            # Incident id → (title, link) so alerts carry "what went down" + a link.
            inc_meta = {
                i["id"]: (i.get("title") or i.get("summary"), i.get("html_url"),
                          i.get("incident_number"))
                for i in incidents
            }

            # Re-check incidents still showing ACK from earlier in the pulse: the
            # PagerDuty window above filters by created_at, so an incident created
            # before this incremental window isn't returned even after it resolves
            # — most are auto-resolved by the alerting integration. Fetch their log
            # entries too so they move ACK→RESOLVED (#stale-ack).
            window_ids = {i["id"] for i in incidents}
            ids_to_fetch = [i["id"] for i in incidents]
            ids_to_fetch += [iid for iid in recheck_ids if iid not in window_ids]

            # Fetch each incident's log entries concurrently (bounded).
            sem = asyncio.Semaphore(10)

            async def _logs(iid: str) -> tuple[str, list[dict[str, Any]]]:
                async with sem:
                    return iid, await pd.log_entries(iid)

            all_logs: dict[str, Any] = {}
            for incident_id, logs in await asyncio.gather(*(_logs(i) for i in ids_to_fetch)):
                all_logs[incident_id] = logs
                title, url, number = inc_meta.get(incident_id, (None, None, None))
                res.alerts.extend(
                    _alerts_from_logs(incident_id, logs, id_to_email, roster, title, url, number)
                )
            res.raw["pagerduty_log_entries.json"] = all_logs
    except Exception:  # noqa: BLE001
        logger.exception("PagerDuty fetch failed")
        res.ok = False
    return res


async def _fetch_github(secrets: Secrets, now: datetime) -> GitHubResult:
    """Per-engineer PR activity for the current pulse (#173).

    For each mapped login, counts PRs created / merged / touched plus PRs
    reviewed within the pulse's date window (``current_pulse``). Inert unless a
    token, a ``GITHUB_ORG`` and at least one mapped login are all present. Search
    rate-limits hard, so concurrency is small (``GITHUB_FETCH_CONCURRENCY``) and
    each login's four queries run sequentially with retry/backoff in the client.
    A per-login failure is isolated; a total failure marks the source down but
    never blocks the rest of the refresh.
    """
    res = GitHubResult()
    logins = config.github_logins()
    if not secrets.github_token or not config.GITHUB_ORG or not logins:
        return res
    # Pulse window is the global anchored 2-week span; the Search API date
    # qualifiers are inclusive, so the upper bound is the pulse's last day.
    _, pstart, pend = current_pulse(now.astimezone(UTC).date())
    until = pend - timedelta(days=1)
    try:
        async with gh_mod.make_async_client(secrets.github_token) as hc:
            gh = gh_mod.GitHubClient(hc)
            sem = asyncio.Semaphore(config.GITHUB_FETCH_CONCURRENCY)

            async def _stats(email: str, login: str) -> tuple[str, GitHubPRStats | None]:
                # Isolate per-login failures (e.g. an unsearchable handle 422s):
                # one bad login must not abort the gather and zero out everyone.
                async with sem:
                    try:
                        return email, await gh.pr_pulse_stats(
                            login, since=pstart, until=until, org=config.GITHUB_ORG
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("GitHub PR stats failed for %s (%s)", login, email)
                        return email, None

            for email, st in await asyncio.gather(
                *(_stats(e, login) for e, login in logins.items())
            ):
                if st is not None:
                    res.pr_stats[email] = st
    except Exception:  # noqa: BLE001
        logger.exception("GitHub fetch failed")
        res.ok = False
    return res


async def _fetch_ical(secrets: Secrets, now: datetime) -> ICalResult:
    res = ICalResult()
    try:
        async with ical_mod.make_async_client() as hc:
            res.raw = await ical_mod.ICalClient(hc).fetch(secrets.pagerduty_ical_url)
        res.oncall = resolve_oncall(res.raw, now.date())
        res.next_oncall = resolve_oncall(res.raw, now.date() + timedelta(days=7))
    except Exception:  # noqa: BLE001
        logger.exception("iCal fetch failed")
        res.ok = False
    return res


async def run_fetch(
    db: Database,
    snapshots: SnapshotWriter,
    secrets: Secrets,
    *,
    now: datetime | None = None,
    window_days: int | None = None,
) -> int:
    """Perform one refresh; persist a snapshot; return its fetch_id."""
    now = now or datetime.now(UTC)
    full_window_start = now - timedelta(days=config.FETCH_WINDOW_DAYS)
    if window_days is not None:
        jira_window_start = pd_window_start = now - timedelta(days=window_days)
    else:
        # Incremental (#88): each source resumes just after its own last
        # successful fetch (a 1h overlap absorbs clock skew / boundary misses);
        # earlier data is preserved by merging the pulse's snapshots — tickets
        # and now alerts both accumulate (see load_merged_data). On a cold start
        # (no prior good fetch) fall back to the full FETCH_WINDOW_DAYS window.
        last_jira = db.latest_good_fetch()
        last_pd = db.latest_pagerduty_fetch()
        jira_window_start = (
            last_jira.fetched_at - timedelta(hours=1)
            if last_jira is not None
            else full_window_start
        )
        pd_window_start = (
            last_pd.fetched_at - timedelta(hours=1)
            if last_pd is not None
            else full_window_start
        )
    roster = set(config.all_roster_emails())

    # Incidents still showing ACK (acked, never seen resolved) from earlier in this
    # pulse. Their created_at predates the incremental PagerDuty window, so re-check
    # them for a resolve that landed since (usually an integration auto-resolve) and
    # move them ACK→RESOLVED (#stale-ack). Empty on a cold/full-window fetch.
    from .counts import accumulated_pulse_alerts, persist_pulse_summaries
    prior_alerts = accumulated_pulse_alerts(db, now)
    recheck_ids = frozenset(
        {a.id for a in prior_alerts if a.state is AlertState.ACKNOWLEDGED}
        - {a.id for a in prior_alerts if a.state is AlertState.RESOLVED}
    )

    jira_res, pd_res, ical_res, gh_res = await asyncio.gather(
        _fetch_jira(secrets, now, jira_window_start, roster),
        _fetch_pagerduty(secrets, now, pd_window_start, roster, recheck_ids),
        _fetch_ical(secrets, now),
        _fetch_github(secrets, now),
    )

    raw_payloads: dict[str, Any] = {**jira_res.raw, **pd_res.raw}
    raw_path = ""
    if raw_payloads or ical_res.raw is not None:
        extra = {"oncall.ics": ical_res.raw} if ical_res.raw is not None else {}
        raw_path = snapshots.write(now, {**raw_payloads, **extra})

    fetch_id = db.create_fetch_snapshot(
        fetched_at=now,
        jira_ok=jira_res.ok,
        pagerduty_ok=pd_res.ok,
        ical_ok=ical_res.ok,
        raw_path=raw_path,
    )
    db.insert_pulses(fetch_id, jira_res.pulses)
    db.insert_tickets(fetch_id, jira_res.tickets)
    db.insert_touches(fetch_id, jira_res.touches)
    db.insert_alerts(fetch_id, pd_res.alerts)
    # Store the current/just-passed + the upcoming weekend's on-call.
    oncalls = [oc for oc in (ical_res.oncall, ical_res.next_oncall) if oc is not None]
    if oncalls:
        db.insert_weekend_oncall(fetch_id, oncalls)
    if gh_res.pr_stats:
        db.insert_github_prs(fetch_id, gh_res.pr_stats)

    # Snapshot this fetch's current + previous pulse totals so the pulse-history
    # table accumulates over time (#80). Persist from the whole pulse's
    # accumulated alerts, not just this fetch's incremental window, so MTTR and
    # alert totals reflect the full pulse (#140). (counts imported above.)
    pulse_alerts = accumulated_pulse_alerts(db, now)
    persist_pulse_summaries(db, jira_res.tickets, pulse_alerts, jira_res.pulses, now)

    # Accumulate this pulse's incidents into the long-lived year history that
    # powers the repeat-offender analysis (#146); idempotent across refreshes.
    from .offenders import incidents_from_alerts
    db.upsert_incidents(incidents_from_alerts(pulse_alerts))

    logger.info(
        "Fetch %s complete: jira_ok=%s pagerduty_ok=%s ical_ok=%s github_ok=%s "
        "tickets=%d alerts=%d oncall=%s gh_engineers=%d gh_created=%d",
        fetch_id, jira_res.ok, pd_res.ok, ical_res.ok, gh_res.ok, len(jira_res.tickets),
        len(pd_res.alerts), ical_res.oncall.engineer_email if ical_res.oncall else None,
        len(gh_res.pr_stats), sum(s.created for s in gh_res.pr_stats.values()),
    )
    return fetch_id
