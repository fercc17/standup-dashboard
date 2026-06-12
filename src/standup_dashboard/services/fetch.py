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
from ..clients import ical as ical_mod
from ..clients import jira as jira_mod
from ..clients import pagerduty as pd_mod
from ..domain.models import Alert, AlertState, Pulse, Ticket, TouchEvent, WeekendOnCall
from ..settings import Secrets
from ..storage.db import Database
from ..storage.snapshots import SnapshotWriter
from .oncall import resolve_oncall
from .pulse import parse_jira_dt, previous_pulse, resolve_pulses
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
    oncall: WeekendOnCall | None = None
    raw: str | None = None


async def _fetch_jira(
    secrets: Secrets, now: datetime, window_start: datetime, roster: set[str]
) -> JiraResult:
    res = JiraResult()
    try:
        async with jira_mod.make_async_client(secrets.jira_token) as hc:
            jira = jira_mod.JiraClient(hc)
            res.pulses = await resolve_pulses(jira, config.PROJECT_KEYS)

            issues_by_key: dict[str, dict[str, Any]] = {}
            for pulse in res.pulses:
                sprint_issues = await jira.sprint_issues(pulse.sprint_id)
                res.raw[f"jira_{pulse.project_key.lower()}_sprint.json"] = sprint_issues
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
) -> list[Alert]:
    out: list[Alert] = []
    state_for = {
        "acknowledge_log_entry": AlertState.ACKNOWLEDGED,
        "resolve_log_entry": AlertState.RESOLVED,
    }
    for entry in log_entries:
        state = state_for.get(entry.get("type", ""))
        if state is None:
            continue
        agent = entry.get("agent") or {}
        email = id_to_email.get(agent.get("id", ""))
        at = parse_jira_dt(entry.get("created_at"))
        if email and email in roster and at is not None:
            out.append(Alert(id=incident_id, handler_email=email, state=state, at=at,
                             title=title, url=url))
    return out


async def _fetch_pagerduty(
    secrets: Secrets, now: datetime, since: datetime, roster: set[str]
) -> PagerDutyResult:
    res = PagerDutyResult()
    try:
        async with pd_mod.make_async_client(secrets.pagerduty_token) as hc:
            pd = pd_mod.PagerDutyClient(hc)
            users = await pd.list_users()
            id_to_email = {u["id"]: u.get("email", "") for u in users}
            # Never request incidents from before the hard floor (FR: PagerDuty
            # window starts no earlier than June 11).
            since = max(since, config.PAGERDUTY_MIN_SINCE)
            # Scope to the roster's PagerDuty team(s) so we don't pull the whole org.
            incidents = await pd.incidents(since, now, team_ids=config.PAGERDUTY_TEAM_IDS)
            res.raw["pagerduty_incidents.json"] = incidents
            # Incident id → (title, link) so alerts carry "what went down" + a link.
            inc_meta = {
                i["id"]: (i.get("title") or i.get("summary"), i.get("html_url"))
                for i in incidents
            }

            # Fetch each incident's log entries concurrently (bounded).
            sem = asyncio.Semaphore(10)

            async def _logs(inc: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
                async with sem:
                    return inc["id"], await pd.log_entries(inc["id"])

            all_logs: dict[str, Any] = {}
            for incident_id, logs in await asyncio.gather(*(_logs(i) for i in incidents)):
                all_logs[incident_id] = logs
                title, url = inc_meta.get(incident_id, (None, None))
                res.alerts.extend(
                    _alerts_from_logs(incident_id, logs, id_to_email, roster, title, url)
                )
            res.raw["pagerduty_log_entries.json"] = all_logs
    except Exception:  # noqa: BLE001
        logger.exception("PagerDuty fetch failed")
        res.ok = False
    return res


async def _fetch_ical(secrets: Secrets, now: datetime) -> ICalResult:
    res = ICalResult()
    try:
        async with ical_mod.make_async_client() as hc:
            res.raw = await ical_mod.ICalClient(hc).fetch(secrets.pagerduty_ical_url)
        res.oncall = resolve_oncall(res.raw, now.date())
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
    if window_days is not None:
        window_start = now - timedelta(days=window_days)
    else:
        # Incremental (#88): query only since the last good fetch (with a small
        # overlap); earlier data is preserved by merging the pulse's snapshots.
        last_good = db.latest_good_fetch()
        window_start = (
            last_good.fetched_at - timedelta(hours=1)
            if last_good is not None
            else now - timedelta(days=config.FETCH_WINDOW_DAYS)
        )
    roster = set(config.all_roster_emails())

    jira_res, pd_res, ical_res = await asyncio.gather(
        _fetch_jira(secrets, now, window_start, roster),
        _fetch_pagerduty(secrets, now, window_start, roster),
        _fetch_ical(secrets, now),
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
    if ical_res.oncall is not None:
        db.insert_weekend_oncall(fetch_id, [ical_res.oncall])

    logger.info(
        "Fetch %s complete: jira_ok=%s pagerduty_ok=%s ical_ok=%s tickets=%d alerts=%d oncall=%s",
        fetch_id, jira_res.ok, pd_res.ok, ical_res.ok, len(jira_res.tickets),
        len(pd_res.alerts), ical_res.oncall.engineer_email if ical_res.oncall else None,
    )
    return fetch_id
