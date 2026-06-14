"""Per-day pulse counts table (FR-020/021/022/023/024, redesigned in #91).

One row per region-local calendar day of the pulse, with Saturday+Sunday merged
into a single weekend row (shown on Monday), plus a trailing "Pulse total" row.

Ticket columns are scoped to one project (``COUNTS_PROJECT`` — ISReq, where
Highest / [PR/MP Review] / ps5-blocker work lives, #91) AND to the selected
region(s). A ticket's region is fixed at **creation** by a follow-the-sun
UTC-hour window (``config.region_for_creation``), independent of who later takes
it; both its "new" and "closed" counts follow that region. Split into two groups
(#91):

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
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.models import (
    PULSE_SUMMARY_FIELDS,
    Alert,
    AlertState,
    Cell,
    CountsRow,
    Pulse,
    Ticket,
)
from .pulse import current_pulse, previous_pulse

# Project whose tickets feed the counts table's New/Closed columns. Highest,
# [PR/MP Review] and ps5-blocker work all live in ISReq (#91); switch here to
# retarget the whole ticket section.
COUNTS_PROJECT = config.PROJECT_ISREQ

# Alert-fatigue thresholds: the on-call standard is 2 alerts per 12h shift, so a
# day exceeding that is flagged red in the table. A weekday row is one 12h shift
# (limit 2); a weekend row merges Sat+Sun into a single 48h on-call shift = four
# 12h shifts, so its limit is 4 × 2 = 8. "More than" the limit (strictly) is red.
ALERT_FATIGUE_WEEKDAY = 2
ALERT_FATIGUE_WEEKEND = 8
# Per-pulse equivalent: a pulse is one Jira sprint = PULSE_LENGTH_DAYS (14) days,
# i.e. 14 × 2 = 28 twelve-hour cycles, so the limit is the 2-per-12h standard
# times 28 = 56 alerts. Used to flag a fatigued pulse red in the pulse-history
# table (mirrors how ALERT_FATIGUE_WEEKEND = 2 × 48h⁄12h was derived).
ALERT_FATIGUE_PULSE = ALERT_FATIGUE_WEEKDAY * (config.PULSE_LENGTH_DAYS * 2)


def _local_date(dt: datetime, zone: ZoneInfo) -> date:
    return dt.astimezone(zone).date()


def _handler_zone(email: str) -> ZoneInfo | None:
    region_key = config.primary_region_for(email)
    return ZoneInfo(config.REGIONS[region_key].timezone) if region_key else None


def _creation_region(t: Ticket) -> str | None:
    """Region a ticket belongs to, fixed at creation (follow-the-sun)."""
    return config.region_for_creation(t.created) if t.created is not None else None


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
    """Region-local days of the current pulse, capped at today.

    The window is the sprint span intersected with the anchored pulse-calendar
    window (#93), so days (and the closes/news bucketed into them) never reach
    back into a prior pulse whose tickets Jira rolled into this sprint.
    """
    if not pulses:
        return []
    today = now.astimezone(zone).date()
    sprint_start = min(p.start for p in pulses).astimezone(zone).date()
    sprint_end = max(p.end for p in pulses).astimezone(zone).date()
    _, pulse_start, pulse_end_excl = current_pulse(today)
    d = max(sprint_start, pulse_start)
    last = min(sprint_end, pulse_end_excl - timedelta(days=1), today)
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


def _alert_mttr(alerts: list[Alert], members: set[str], dates: set[date]) -> tuple[int, int]:
    """(sum_seconds, n_incidents) of time from first ack to resolution.

    Considers only incidents whose ack *and* resolve were handled by ``members``
    within ``dates`` (handler-tz bucketed — the same scope as the alert counts).
    Persisting sum + count (not a median) keeps the pulse MTTR composable across
    regions: the mean = sum/n sums cleanly, whereas a median would not.
    """
    ack_at: dict[str, datetime] = {}
    res_at: dict[str, datetime] = {}
    for a in alerts:
        if a.handler_email not in members:
            continue
        zone = _handler_zone(a.handler_email)
        if zone is None or _local_date(a.at, zone) not in dates:
            continue
        if a.state is AlertState.ACKNOWLEDGED:
            bucket = ack_at
        elif a.state is AlertState.RESOLVED:
            bucket = res_at
        else:
            continue
        if a.id not in bucket or a.at < bucket[a.id]:  # earliest event of each kind
            bucket[a.id] = a.at
    total = n = 0
    for incident_id, resolved in res_at.items():
        acked = ack_at.get(incident_id)
        if acked is not None and resolved >= acked:
            total += int((resolved - acked).total_seconds())
            n += 1
    return total, n


def _alert_mtta(alerts: list[Alert], members: set[str], dates: set[date]) -> tuple[int, int]:
    """(sum_seconds, n_incidents) of time from incident trigger to first ack.

    Pairs each incident's earliest trigger (a handler-less TRIGGERED event) with
    the earliest acknowledgement by ``members`` within ``dates`` (bucketed in the
    acker's region tz — the same scope as the alert counts). Sum+count keeps the
    pulse MTTA composable across regions, mirroring ``_alert_mttr``.
    """
    trig_at: dict[str, datetime] = {}
    ack_at: dict[str, datetime] = {}
    for a in alerts:
        if a.state is AlertState.TRIGGERED:
            if a.id not in trig_at or a.at < trig_at[a.id]:  # earliest fire
                trig_at[a.id] = a.at
            continue
        if a.state is not AlertState.ACKNOWLEDGED or a.handler_email not in members:
            continue
        zone = _handler_zone(a.handler_email)
        if zone is None or _local_date(a.at, zone) not in dates:
            continue
        if a.id not in ack_at or a.at < ack_at[a.id]:  # earliest ack by a member
            ack_at[a.id] = a.at
    total = n = 0
    for incident_id, acked in ack_at.items():
        triggered = trig_at.get(incident_id)
        if triggered is not None and acked >= triggered:
            total += int((acked - triggered).total_seconds())
            n += 1
    return total, n


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


def _assignee(t: Ticket) -> str | None:
    return t.assignee_email


def _reporter(t: Ticket) -> str | None:
    return t.reporter_email


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
    today = now.astimezone(axis_zone).date()
    days = pulse_dates(pulses, axis_zone, now)
    groups = _group_days(days)

    selected_set = set(selected_regions)
    selected_members: set[str] = set()
    for key in selected_regions:
        selected_members.update(config.REGIONS[key].member_emails)
    # Global denominator = all counted roster members (excludes management).
    counted_members = {e.email for e in config.ROSTER if config.is_counted(e)}

    scoped = [t for t in tickets if t.project_key == COUNTS_PROJECT]

    def _new_on(t: Ticket, dates: set[date]) -> bool:
        # A "new" ticket belongs to the region its creation-time falls in
        # (follow-the-sun), bucketed on that region's local creation day.
        region = _creation_region(t)
        if region is None or region not in selected_set:
            return False
        zone = ZoneInfo(config.REGIONS[region].timezone)
        return _local_date(t.created, zone) in dates

    def _closed_on(t: Ticket, dates: set[date]) -> bool:
        # Closes credit the ticket's creation-time region (fixed at creation).
        region = _creation_region(t)
        return region in selected_set and t.is_done_date in dates

    def _row(label: str, dset: set[date], *, is_weekend: bool, is_total: bool) -> CountsRow:
        new_tickets = [t for t in scoped if _new_on(t, dset)]
        buckets: dict[str, list[Ticket]] = {"highest": [], "pr_mp": [], "ps5": [], "regular": []}
        for t in new_tickets:
            buckets[_new_bucket(t)].append(t)
        closed = [t for t in scoped if _closed_on(t, dset)]
        # Closed [PR/MP Review] is credited to the ASSIGNEE's region (the owner
        # who did the review), not the ticket's creation region.
        closed_pr_mp = [
            t for t in scoped
            if t.is_pr_mp_review and t.assignee_email in selected_members
            and t.is_done_date in dset
        ]

        ack = _alert_cell(alerts, selected_members, dset, AlertState.ACKNOWLEDGED)
        resolved = _alert_cell(alerts, selected_members, dset, AlertState.RESOLVED)
        # Alert fatigue: a real day (not a pulse-total row) whose alert load
        # exceeds the per-shift standard (weekday one shift, weekend = four).
        fatigue_limit = ALERT_FATIGUE_WEEKEND if is_weekend else ALERT_FATIGUE_WEEKDAY
        alert_fatigue = (not is_total) and (ack.count + resolved.count) > fatigue_limit
        region_distinct = _alert_cell(alerts, selected_members, dset, None).count
        global_distinct = _alert_cell(alerts, counted_members, dset, None).count
        pct = (100.0 * region_distinct / global_distinct) if global_distinct else None
        # Closed %: the selected region's share of all ISReq closed that day
        # (denominator = every closed ticket, each owned by its creation region).
        global_closed = sum(
            1 for t in scoped if _creation_region(t) is not None and t.is_done_date in dset
        )
        closed_pct = (100.0 * len(closed) / global_closed) if global_closed else None
        # ISDB closed (count + region share) — separate project column.
        isdb_closed_tickets = [
            t for t in tickets
            if t.is_isdb and _creation_region(t) in selected_set and t.is_done_date in dset
        ]
        global_isdb_closed = sum(
            1 for t in tickets
            if t.is_isdb and _creation_region(t) is not None and t.is_done_date in dset
        )
        isdb_closed_pct = (
            100.0 * len(isdb_closed_tickets) / global_isdb_closed if global_isdb_closed else None
        )

        return CountsRow(
            label=label,
            is_weekend=is_weekend,
            is_total=is_total,
            new_highest=_ticket_cell(buckets["highest"], _assignee),
            # New [PR/MP Review] credits the REQUESTER (reporter) who raised it (#141).
            new_pr_mp=_ticket_cell(buckets["pr_mp"], _reporter),
            new_ps5=_ticket_cell(buckets["ps5"], _assignee),
            new_regular=_ticket_cell(buckets["regular"], _assignee),
            new_total=_ticket_cell(new_tickets, _assignee),
            closed_highest=_ticket_cell([t for t in closed if t.is_highest], _assignee),
            closed_pr_mp=_ticket_cell(closed_pr_mp, _assignee),
            closed_ps5=_ticket_cell([t for t in closed if t.has_ps5_blockers], _assignee),
            closed_total=_ticket_cell(closed, _assignee),
            isdb_closed=_ticket_cell(isdb_closed_tickets, _assignee),
            alerts_ack=ack,
            alerts_resolved=resolved,
            alerts_total=_merge_cells([ack, resolved]),
            region_alert_pct=pct,
            closed_pct=closed_pct,
            isdb_closed_pct=isdb_closed_pct,
            alert_fatigue=alert_fatigue,
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

        # Previous-pulse comparison (#80): same buckets over the prior pulse's
        # window. Ticket data comes from a dedicated fetch; alerts that far back
        # usually aren't collected, so they read 0.
        prev_num, prev_start, prev_end = previous_pulse(today)
        prev_dates = {
            prev_start + timedelta(days=i) for i in range((prev_end - prev_start).days)
        }
        prev = _row(f"Previous pulse (P{prev_num})", prev_dates,
                    is_weekend=False, is_total=True)
        prev.is_previous = True
        prev.region_alert_pct = None
        rows.append(prev)
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


def _window_dates(pulses: list[Pulse], zone: ZoneInfo, now: datetime, previous: bool) -> set[date]:
    if not previous:
        return set(pulse_dates(pulses, zone, now))
    _, start, end = previous_pulse(now.astimezone(zone).date())
    return {start + timedelta(days=i) for i in range((end - start).days)}


def region_pulse_summary(
    region: str, tickets: list[Ticket], alerts: list[Alert], pulses: list[Pulse],
    now: datetime, *, previous: bool = False, dates: set[date] | None = None,
) -> dict[str, Cell]:
    """Per-metric Cells (count + person breakdown) for one region's pulse (#80).

    Attribution per the requested tooltips: new tickets break down by requestor
    (reporter), including [PR/MP Review] (#141); closed by assignee; alerts by
    handler.

    ``dates`` overrides the window with an explicit set of region-local calendar
    days (used by the historical backfill); when omitted it is derived from the
    pulse calendar as usual.
    """
    zone = ZoneInfo(config.REGIONS[region].timezone)
    if dates is None:
        dates = _window_dates(pulses, zone, now, previous)
    members = set(config.REGIONS[region].member_emails)
    scoped = [t for t in tickets if t.project_key == COUNTS_PROJECT]

    def _new(t: Ticket) -> bool:
        # Region by creation-time window (follow-the-sun), bucketed on the
        # region's local creation day.
        if _creation_region(t) != region:
            return False
        return _local_date(t.created, zone) in dates

    new_tickets = [t for t in scoped if _new(t)]
    buckets: dict[str, list[Ticket]] = {"highest": [], "pr_mp": [], "ps5": [], "regular": []}
    for t in new_tickets:
        buckets[_new_bucket(t)].append(t)
    closed = [
        t for t in scoped if _creation_region(t) == region and t.is_done_date in dates
    ]
    # Closed [PR/MP Review] credited to the assignee's (owner's) region.
    closed_pr_mp = [
        t for t in scoped
        if t.is_pr_mp_review and t.assignee_email in members and t.is_done_date in dates
    ]
    isdb_closed = [
        t for t in tickets
        if t.is_isdb and _creation_region(t) == region and t.is_done_date in dates
    ]
    ack = _alert_cell(alerts, members, dates, AlertState.ACKNOWLEDGED)
    res = _alert_cell(alerts, members, dates, AlertState.RESOLVED)
    mttr_sum, mttr_n = _alert_mttr(alerts, members, dates)
    mtta_sum, mtta_n = _alert_mtta(alerts, members, dates)
    return {
        "new_highest": _ticket_cell(buckets["highest"], _reporter),
        "new_pr_mp": _ticket_cell(buckets["pr_mp"], _reporter),   # requester (#141)
        "new_ps5": _ticket_cell(buckets["ps5"], _reporter),
        "new_regular": _ticket_cell(buckets["regular"], _reporter),
        "new_total": _ticket_cell(new_tickets, _reporter),
        "closed_highest": _ticket_cell([t for t in closed if t.is_highest], _assignee),
        "closed_pr_mp": _ticket_cell(closed_pr_mp, _assignee),
        "closed_ps5": _ticket_cell([t for t in closed if t.has_ps5_blockers], _assignee),
        "closed_total": _ticket_cell(closed, _assignee),
        "isdb_closed": _ticket_cell(isdb_closed, _assignee),
        "alerts_ack": ack,
        "alerts_resolved": res,
        "alerts_total": _merge_cells([ack, res]),
        # Accumulators for mean time-to-resolve / -acknowledge (sum/n), composable.
        "alert_mttr_sum": Cell(count=mttr_sum),
        "alert_mttr_n": Cell(count=mttr_n),
        "alert_mtta_sum": Cell(count=mtta_sum),
        "alert_mtta_n": Cell(count=mtta_n),
    }


def combine_summaries(summaries: list[dict[str, Cell]]) -> dict[str, Cell]:
    """Merge per-region summaries into one (sum counts, merge breakdowns)."""
    return {m: _merge_cells([s[m] for s in summaries]) for m in PULSE_SUMMARY_FIELDS}


def accumulated_alerts_since(db, since: datetime) -> list[Alert]:
    """De-duplicated PagerDuty alerts from every PD-ok snapshot fetched ≥ ``since``.

    Dedup by (incident, handler, state, time), preferring the enriched copy (with
    incident title/number). Shared by pulse-summary persistence (#140) and the
    weekend recap (#145), which both need alerts spanning more than the last fetch.
    """
    by_key: dict[tuple, Alert] = {}
    for snap in db.fetches_since(since):
        if not snap.pagerduty_ok:
            continue
        for a in db.get_alerts(snap.id):
            key = (a.id, a.handler_email, a.state, a.at)
            existing = by_key.get(key)
            if existing is None or (a.title and not existing.title):
                by_key[key] = a
    return list(by_key.values())


def accumulated_pulse_alerts(db, now: datetime) -> list[Alert]:
    """Accumulated alerts across the current pulse's fetches (since pulse start).

    PagerDuty is fetched incrementally, so persisting summaries from a single
    fetch made the MTTR column read blank (#140); persist from this instead.
    """
    _, start, _ = current_pulse(now.astimezone(UTC).date())
    pulse_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    return accumulated_alerts_since(db, pulse_start)


def persist_pulse_summaries(db, tickets, alerts, pulses, now: datetime) -> None:
    """Store the current + previous pulse totals + breakdowns per region so the
    pulse-history table accumulates across pulses (#80).

    ``alerts`` should be the accumulated pulse alerts (see
    ``accumulated_pulse_alerts``), not a single fetch's window (#140)."""
    if not pulses:
        return
    for region in config.REGION_KEYS:
        zone = ZoneInfo(config.REGIONS[region].timezone)
        cur_num, _, _ = current_pulse(now.astimezone(zone).date())
        # Current pulse is authoritative (replace); the previous pulse only fills
        # a gap (replace=False) so a refresh whose live window no longer covers
        # it — e.g. its alerts predate the PagerDuty floor — can't wipe a stored
        # (backfilled / earlier current-phase) summary down to zero.
        for num, prev in ((cur_num, False), (cur_num - 1, True)):
            cells = region_pulse_summary(region, tickets, alerts, pulses, now, previous=prev)
            counts = {m: c.count for m, c in cells.items()}
            breakdowns = {m: c.breakdown for m, c in cells.items()}
            db.upsert_pulse_summary(num, region, counts, breakdowns, now, replace=not prev)
