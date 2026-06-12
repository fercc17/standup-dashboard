"""Per-day pulse counts table (FR-020/021/022/023/024) — T038 + T043.

One row per region-local calendar day of the pulse, with Saturday+Sunday merged
into a single weekend row (shown on Monday). Columns (FR-021):

  open Highest ISReq*, new Highest ISReq (24h)*, ISDB completed that day,
  open ps5-blockers*, new ps5-blockers (24h)*, alerts ack, alerts resolved,
  total, region % of the global (3-region, deduped) alert total that day.

Columns marked * are fetch-time snapshots / 24h figures attached to the most
recent (today) row only. Ticket columns are project-wide (not region-scoped);
alert columns are scoped to the selected regions' members. When multiple
regions are selected, alerts are deduplicated by incident id, each handler is
bucketed in their own region's timezone (FR-022/024), and the percentage
denominator is the deduplicated three-region total (excluding Global managers,
FR-004).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.models import (
    Alert,
    AlertState,
    CountsRow,
    Pulse,
    Ticket,
    TicketGroup,
)

_24H = timedelta(hours=24)


def _local_date(dt: datetime, zone: ZoneInfo) -> date:
    return dt.astimezone(zone).date()


def _handler_zone(email: str) -> ZoneInfo | None:
    region_key = config.primary_region_for(email)
    return ZoneInfo(config.REGIONS[region_key].timezone) if region_key else None


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


def _isdb_completed(tickets: list[Ticket], dates: set[date]) -> int:
    return sum(1 for t in tickets if t.is_isdb and t.is_done_date in dates)


def _incident_ids(
    alerts: list[Alert], members: set[str], dates: set[date], state: AlertState | None
) -> set[str]:
    """Distinct incident ids handled by ``members`` on ``dates`` (handler-tz bucketed).

    ``state=None`` matches any state (used for the global denominator).
    """
    out: set[str] = set()
    for a in alerts:
        if a.handler_email not in members:
            continue
        if state is not None and a.state is not state:
            continue
        zone = _handler_zone(a.handler_email)
        if zone is None:
            continue
        if _local_date(a.at, zone) in dates:
            out.add(a.id)
    return out


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
    today = now.astimezone(axis_zone).date()

    selected_members: set[str] = set()
    for key in selected_regions:
        selected_members.update(config.REGIONS[key].member_emails)
    # Global denominator = all non-Global roster members (FR-004).
    counted_members = {e.email for e in config.ROSTER if not e.is_global}

    # Fetch-time snapshot / 24h figures (FR-021 *), attached to today's row.
    open_highest = sum(
        1 for t in tickets if t.is_isreq and t.is_highest and t.group is not TicketGroup.SUCCESS
    )
    open_ps5 = sum(
        1 for t in tickets if t.has_ps5_blockers and t.group is not TicketGroup.SUCCESS
    )
    new_highest = sum(
        1 for t in tickets if t.is_isreq and t.is_highest and t.created and t.created >= now - _24H
    )
    new_ps5 = sum(
        1 for t in tickets if t.has_ps5_blockers and t.created and t.created >= now - _24H
    )
    open_pr_mp = sum(
        1 for t in tickets if t.is_bvg_review and t.group is not TicketGroup.SUCCESS
    )

    rows: list[CountsRow] = []
    for label, dates_list, is_weekend in groups:
        dset = set(dates_list)
        ack = _incident_ids(alerts, selected_members, dset, AlertState.ACKNOWLEDGED)
        resolved = _incident_ids(alerts, selected_members, dset, AlertState.RESOLVED)
        region_ids = ack | resolved
        global_ids = _incident_ids(alerts, counted_members, dset, None)
        pct = (100.0 * len(region_ids) / len(global_ids)) if global_ids else None

        is_today_row = today in dset
        rows.append(CountsRow(
            label=label,
            is_weekend=is_weekend,
            open_highest_isreq=open_highest if is_today_row else 0,
            new_highest_isreq_24h=new_highest if is_today_row else 0,
            isdb_completed=_isdb_completed(tickets, dset),
            open_ps5_blockers=open_ps5 if is_today_row else 0,
            new_ps5_blockers_24h=new_ps5 if is_today_row else 0,
            alerts_ack=len(ack),
            alerts_resolved=len(resolved),
            alerts_total=len(ack) + len(resolved),
            region_alert_pct=pct,
            open_pr_mp_review=open_pr_mp if is_today_row else 0,
        ))

    if rows:
        rows.append(CountsRow(
            label="Pulse total",
            is_weekend=False,
            is_total=True,
            open_highest_isreq=open_highest,
            new_highest_isreq_24h=new_highest,
            isdb_completed=sum(r.isdb_completed for r in rows),
            open_ps5_blockers=open_ps5,
            new_ps5_blockers_24h=new_ps5,
            alerts_ack=sum(r.alerts_ack for r in rows),
            alerts_resolved=sum(r.alerts_resolved for r in rows),
            alerts_total=sum(r.alerts_total for r in rows),
            region_alert_pct=None,
            open_pr_mp_review=open_pr_mp,
        ))
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
