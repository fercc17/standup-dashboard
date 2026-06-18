"""Ticket parsing + touch attribution (FR-014) — T023.

Builds ``Ticket`` objects from raw Jira issues and derives ``TouchEvent``s from
changelog (status / assignment / link), comments, and worklogs within the pulse
window. Attribution is by author email; entries without an email (or outside the
window, or by non-roster users) are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from .. import config
from ..domain.models import (
    STATUS_CATEGORY_GROUP,
    STATUS_GROUP,
    Ticket,
    TicketGroup,
    TouchEvent,
    TouchKind,
)
from .pulse import parse_jira_dt


def _canonical_project(issue_key: str) -> str:
    """Map a Jira key prefix (e.g. ``ISREQ-1837``) to the canonical config key.

    Jira returns keys uppercased (``ISREQ``) while config/coloring use ``ISReq``;
    normalize so project comparisons and pulse matching work.
    """
    prefix = issue_key.split("-", 1)[0]
    for key in config.PROJECT_KEYS:
        if key.upper() == prefix.upper():
            return key
    return prefix


def _resolve_email(
    actor: dict[str, Any] | None, account_emails: Mapping[str, str] | None = None
) -> str | None:
    """Roster email for a Jira actor (assignee / reporter / changelog author).

    Atlassian omits ``emailAddress`` from the user object for accounts whose
    email-visibility profile is private, so attribution by email alone silently
    drops those engineers' tickets and touches. Fall back to mapping the always-
    present ``accountId`` to a roster email via ``account_emails`` (#priv-email).
    """
    if not actor:
        return None
    email = actor.get("emailAddress")
    if email:
        return email
    if account_emails:
        return account_emails.get(actor.get("accountId"))
    return None


def seed_account_emails(issues: Iterable[dict[str, Any]]) -> dict[str, str]:
    """accountId → email gathered from actors that DO expose an email.

    A free reverse map: every assignee / reporter / changelog author carrying an
    ``emailAddress`` reveals its ``accountId``↔email pair, so the caller only has
    to query Jira for the accounts still unresolved — typically just the private-
    email users (#priv-email).
    """
    out: dict[str, str] = {}

    def record(actor: dict[str, Any] | None) -> None:
        if not actor:
            return
        acct, email = actor.get("accountId"), actor.get("emailAddress")
        if acct and email:
            out[acct] = email

    for issue in issues:
        fields = issue.get("fields") or {}
        record(fields.get("assignee"))
        record(fields.get("reporter"))
        for hist in (issue.get("changelog") or {}).get("histories", []):
            record(hist.get("author"))
    return out


def parse_ticket(
    issue: dict[str, Any], account_emails: Mapping[str, str] | None = None
) -> Ticket:
    """Map a raw Jira issue (with optional changelog) into a ``Ticket``."""
    fields = issue.get("fields", {})
    status_obj = fields.get("status") or {}
    status = status_obj.get("name", "")
    status_category = (status_obj.get("statusCategory") or {}).get("name")
    priority = (fields.get("priority") or {}).get("name")
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    sprint = fields.get("sprint") or {}
    return Ticket(
        id=issue["key"],
        project_key=_canonical_project(issue["key"]),
        title=fields.get("summary", ""),
        status=status,
        priority=priority,
        labels=list(fields.get("labels", []) or []),
        assignee_email=_resolve_email(assignee, account_emails),
        sprint_id=sprint.get("id") if isinstance(sprint, dict) else None,
        is_done_date=_done_date(issue),
        created=parse_jira_dt(fields.get("created")),
        status_category=status_category,
        reporter_email=_resolve_email(reporter, account_emails),
        wip_since=_wip_since(issue, status, status_category,
                             parse_jira_dt(fields.get("created"))),
    )


def _done_date(issue: dict[str, Any]):
    """Date of the most recent transition into Done, if any (UTC)."""
    latest = None
    for hist in (issue.get("changelog") or {}).get("histories", []):
        at = parse_jira_dt(hist.get("created"))
        if at is None:
            continue
        for item in hist.get("items", []):
            if item.get("field") == "status" and item.get("toString") == "Done":
                if latest is None or at > latest:
                    latest = at
    return latest.date() if latest else None


def _is_wip(status_name: str | None) -> bool:
    return STATUS_GROUP.get(status_name or "") is TicketGroup.WIP


def _wip_since(issue, status, status_category, created):
    """Start of the ticket's *current* continuous In-Progress streak (#147).

    Only meaningful when the ticket is in the WIP group now. Walks the status
    changelog and returns when it last entered WIP from a non-WIP status —
    WIP→WIP moves (In Progress→In Review) don't reset it. Falls back to the most
    recent status change (or creation) for custom WIP status names not in
    ``STATUS_GROUP``."""
    current = STATUS_CATEGORY_GROUP.get(status_category) or STATUS_GROUP.get(status)
    if current is not TicketGroup.WIP:
        return None
    changes = []
    for hist in (issue.get("changelog") or {}).get("histories", []):
        at = parse_jira_dt(hist.get("created"))
        if at is None:
            continue
        for item in hist.get("items", []):
            if item.get("field") == "status":
                changes.append((at, item.get("fromString"), item.get("toString")))
    if not changes:
        return created                       # created straight into WIP, never moved
    changes.sort(key=lambda c: c[0])
    streak = created if _is_wip(changes[0][1]) else None
    for at, _frm, to in changes:
        if _is_wip(to):
            if streak is None:
                streak = at                  # entered WIP from non-WIP
        else:
            streak = None                    # left WIP
    return streak if streak is not None else changes[-1][0]


_CHANGELOG_KIND = {"status": TouchKind.STATUS, "assignee": TouchKind.ASSIGNMENT}


def extract_touches(
    issue: dict[str, Any],
    *,
    comments: list[dict[str, Any]] | None = None,
    worklogs: list[dict[str, Any]] | None = None,
    window_start: datetime,
    window_end: datetime,
    roster_emails: set[str],
    account_emails: Mapping[str, str] | None = None,
) -> list[TouchEvent]:
    """Derive per-engineer touch events for one issue inside the pulse window."""
    key = issue["key"]
    seen: set[tuple[str, TouchKind, datetime, int]] = set()
    out: list[TouchEvent] = []

    def add(email: str | None, kind: TouchKind, at: datetime | None, seconds: int = 0) -> None:
        if not email or at is None or email not in roster_emails:
            return
        if not (window_start <= at <= window_end):
            return
        sig = (email, kind, at, seconds)
        if sig in seen:
            return
        seen.add(sig)
        out.append(TouchEvent(ticket_id=key, engineer_email=email, kind=kind, at=at,
                              seconds=seconds))

    # Changelog: status / assignment / link.
    for hist in (issue.get("changelog") or {}).get("histories", []):
        email = _resolve_email(hist.get("author"), account_emails)
        at = parse_jira_dt(hist.get("created"))
        for item in hist.get("items", []):
            field = (item.get("field") or "").lower()
            if field in _CHANGELOG_KIND:
                add(email, _CHANGELOG_KIND[field], at)
            elif field == "link":
                add(email, TouchKind.LINK, at)

    # Comments.
    for c in comments or []:
        add(_resolve_email(c.get("author"), account_emails), TouchKind.COMMENT,
            parse_jira_dt(c.get("created")))

    # Worklogs: Tempo records them under a bot author, so attribute the logged
    # time to the ticket's assignee (proxy) and carry the duration (#167).
    assignee = _resolve_email((issue.get("fields") or {}).get("assignee"), account_emails)
    for w in worklogs or []:
        add(assignee, TouchKind.WORKLOG, parse_jira_dt(w.get("started")),
            int(w.get("timeSpentSeconds") or 0))

    return out
