"""Per-day pulse counts table (FR-020/021/022/023/024, redesigned in #91).

One row per region-local calendar day of the pulse, with Saturday+Sunday merged
into a single weekend row (shown on Monday), plus a trailing "Pulse total" row.

Ticket columns are ISDB-scoped and split into two groups (#91):

  * New that day, four mutually exclusive buckets (precedence
    Highest → [PR/MP Review] → ps5-blocker → regular) that sum to "New total".
  * Closed that day: Highest, ps5-blocker (subcounts) and the closed total.

Alert columns (Alerts Ack / Alert Res / Total + region % of the global total)
are scoped to the selected regions' members, deduplicated by incident id, each
handler bucketed in their own region timezone (FR-022/024). The percentage
denominator is the deduplicated total over all counted members (excluding
management — FR-004 / #72).

Every number carries a per-person breakdown for its tooltip: reporter for new
tickets, assignee for closed tickets, handler for alerts.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.models import (
    Alert,
    AlertState,
    Cell,
    CountsRow,
    Pulse,
    Ticket,
)


def _local_date(dt: datetime, zone: ZoneInfo) -> date:
    return dt.astimezone(zone).date()


def _handler_zone(email: str) -> ZoneInfo | None:
    region_key = config.primary_region_for(email)
    return ZoneInfo(config.REGIONS[region_key].timezone) if region_key else None


def _display_name(email: str | None) -> str:
    """Human label for a tooltip: roster name, else derived from the email."""
    if not email:
        return "Unassigned"
    eng = config.ENGINEERS_BY_EMAIL.get(email)
    if eng:
        return eng.name
    parts = [p for p in re.split(r"[._-]+", email.split("@", 1)[0]) if p]
    return " ".join(p.capitalize() for p in parts) if parts else email


def pulse_dates(pulses: list[Pulse], zone: ZoneInfo, now: datetime) -> list[date]:
    """Region-local calendar days of the pulse, capped at today."""
    if not pulses:
        return []
    start = min(p.start for p in pulses)
    end = max(p.end for p in pulses)
    d = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    today = now.astimezone(zone).date()
    if last > today:
        last = today
    days: list[date] = []
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


def _group_days(days: list[date]) -> list[tuple[str, list[date], bool]]:
    """Collapse Sat+Sun into one weekend group; weekdays stay singular."""
    groups: list[tuple[str, list[date], bool]] = []
    i = 0
    while i < len(days):
        d = days[i]
        if d.weekday() == 5 and i + 1 < len(days) and days[i + 1].weekday() == 6:
            sat, sun = d, days[i + 1]
            groups.append((f"Sat–Sun {sat:%d}–{sun:%d %b}", [sat, sun], True))
            i += 2
        elif d.weekday() in (5, 6):
            groups.append((f"{d:%a %d %b}", [d], True))
            i += 1
        else:
            groups.append((f"{d:%a %d %b}", [d], False))
            i += 1
    return groups


def _ticket_cell(tickets: list[Ticket], email_of) -> Cell:
    """A Cell for a set of tickets, broken down by ``email_of`` (reporter/assignee)."""
    breakdown: dict[str, int] = {}
    for t in tickets:
        name = _display_name(email_of(t))
        breakdown[name] = breakdown.get(name, 0) + 1
    return Cell(count=len(tickets), breakdown=breakdown)


def _alert_cell(
    alerts: list[Alert], members: set[str], dates: set[date], state: AlertState | None
) -> Cell:
    """Distinct incidents handled by ``members`` on ``dates`` (handler-tz bucketed).

    ``state=None`` matches any state. The breakdown maps handler → distinct
    incidents they handled.
    """
    ids: set[str] = set()
    per_person: dict[str, set[str]] = {}
    for a in alerts:
        if a.handler_email not in members:
            continue
        if state is not None and a.state is not state:
            continue
        zone = _handler_zone(a.handler_email)
        if zone is None:
            continue
        if _local_date(a.at, zone) in dates:
            ids.add(a.id)
            per_person.setdefault(_display_name(a.handler_email), set()).add(a.id)
    return Cell(count=len(ids), breakdown={n: len(s) for n, s in per_person.items()})


def _merge_cells(cells: list[Cell]) -> Cell:
    """Element-wise sum of cells (count + per-person breakdown)."""
    breakdown: dict[str, int] = {}
    for c in cells:
        for name, n in c.breakdown.items():
            breakdown[name] = breakdown.get(name, 0) + n
    return Cell(count=sum(c.count for c in cells), breakdown=breakdown)


def _new_bucket(ticket: Ticket) -> str:
    """Exactly one new-ticket bucket, by precedence (so the four sum to total)."""
    if ticket.is_highest:
        return "highest"
    if ticket.is_pr_mp_review:
        return "pr_mp"
    if ticket.has_ps5_blockers:
        return "ps5"
    return "regular"


def _reporter(t: Ticket) -> str | None:
    return t.reporter_email


def _assignee(t: Ticket) -> str | None:
    return t.assignee_email


def build_counts(
    selected_regions: list[str],
    tickets: list[Ticket],
    alerts: list[Alert],
    pulses: list[Pulse],
    now: datetime,
) -> list[CountsRow]:
    if not selected_regions:
        return []

    axis_zone = ZoneInfo(config.REGIONS[selected_regions[0]].timezone)
    days = pulse_dates(pulses, axis_zone, now)
    groups = _group_days(days)

    selected_members: set[str] = set()
    for key in selected_regions:
        selected_members.update(config.REGIONS[key].member_emails)
    # Global denominator = all counted roster members (excludes management).
    counted_members = {e.email for e in config.ROSTER if config.is_counted(e)}

    isdb = [t for t in tickets if t.is_isdb]

    def _created_on(t: Ticket, dates: set[date]) -> bool:
        return t.created is not None and _local_date(t.created, axis_zone) in dates

    def _row(label: str, dset: set[date], *, is_weekend: bool, is_total: bool) -> CountsRow:
        new_tickets = [t for t in isdb if _created_on(t, dset)]
        buckets: dict[str, list[Ticket]] = {"highest": [], "pr_mp": [], "ps5": [], "regular": []}
        for t in new_tickets:
            buckets[_new_bucket(t)].append(t)
        closed = [t for t in isdb if t.is_done_date in dset]

        ack = _alert_cell(alerts, selected_members, dset, AlertState.ACKNOWLEDGED)
        resolved = _alert_cell(alerts, selected_members, dset, AlertState.RESOLVED)
        region_distinct = _alert_cell(alerts, selected_members, dset, None).count
        global_distinct = _alert_cell(alerts, counted_members, dset, None).count
        pct = (100.0 * region_distinct / global_distinct) if global_distinct else None

        return CountsRow(
            label=label,
            is_weekend=is_weekend,
            is_total=is_total,
            new_highest=_ticket_cell(buckets["highest"], _reporter),
            new_pr_mp=_ticket_cell(buckets["pr_mp"], _reporter),
            new_ps5=_ticket_cell(buckets["ps5"], _reporter),
            new_regular=_ticket_cell(buckets["regular"], _reporter),
            new_total=_ticket_cell(new_tickets, _reporter),
            closed_highest=_ticket_cell([t for t in closed if t.is_highest], _assignee),
            closed_ps5=_ticket_cell([t for t in closed if t.has_ps5_blockers], _assignee),
            closed_total=_ticket_cell(closed, _assignee),
            alerts_ack=ack,
            alerts_resolved=resolved,
            alerts_total=_merge_cells([ack, resolved]),
            region_alert_pct=pct,
        )

    rows: list[CountsRow] = []
    all_dates: set[date] = set()
    for label, dates_list, is_weekend in groups:
        dset = set(dates_list)
        all_dates |= dset
        rows.append(_row(label, dset, is_weekend=is_weekend, is_total=False))

    if rows:
        total = _row("Pulse total", all_dates, is_weekend=False, is_total=True)
        total.region_alert_pct = None  # a pulse-wide region share isn't meaningful here
        rows.append(total)
    return rows


def build_region_counts(
    region_key: str,
    tickets: list[Ticket],
    alerts: list[Alert],
    pulses: list[Pulse],
    now: datetime,
) -> list[CountsRow]:
    """Single-region convenience wrapper (US3)."""
    return build_counts([region_key], tickets, alerts, pulses, now)
