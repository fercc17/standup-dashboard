"""Presentation view models built from stored fetch data (FR-018/019) — T026.

Pure-ish glue: loads a fetch layer from SQLite, resolves each engineer's
effective role in their region timezone, and assembles chips + detail panels,
applying the tested color matrix. Multi-region grouping/dedup (US4) and the
counts table (US3) extend this module in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.coloring import (
    ALERT_MTTA_GREEN_S,
    ALERT_MTTA_YELLOW_S,
    ALERT_MTTR_GREEN_S,
    ALERT_MTTR_YELLOW_S,
    ALERT_RES_GREEN,
    ALERT_RES_YELLOW,
    alert_classification,
    alert_color,
    closed_vs_new_level,
    closed_vs_new_total_level,
    count_level,
    cycle_color,
    intake_level,
    is_role_distractor,
    mtta_trend_level,
    mttr_trend_level,
    pr_mp_review_level,
    resolve_rate_level,
    ticket_color,
)
from ..domain.models import (
    PRIORITY_HIGHEST,
    PS5_BLOCKER_LABELS,
    PULSE_SUMMARY_FIELDS,
    Alert,
    AlertState,
    Cell,
    ChipVM,
    Color,
    CountsRow,
    DetailPanelVM,
    Pulse,
    PulseHistoryRow,
    Role,
    Ticket,
    TicketGroup,
    TicketVM,
    TouchEvent,
    TouchKind,
    WeekendOnCall,
    format_duration,
)
from ..domain.roles import effective_role, is_weekend
from ..services.classification import classify_for_engineer, in_scope
from ..services.counts import (
    ALERT_FATIGUE_PULSE,
    ALERT_FATIGUE_WEEKDAY,
    ALERT_FATIGUE_WEEKEND,
    _display_name,
    _handler_zone,
    accumulated_alerts_since,
    combine_summaries,
    region_pulse_summary,
)
from ..services.counts import build_counts as _build_counts
from ..services.oncall import others_off
from ..services.pulse import current_pulse
from ..storage.db import Database

_24H = timedelta(hours=24)


@dataclass
class DashboardData:
    fetched_at: datetime
    tickets: list[Ticket] = field(default_factory=list)
    touches: list[TouchEvent] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    pulses: list[Pulse] = field(default_factory=list)
    weekend_oncall: list[WeekendOnCall] = field(default_factory=list)

    @property
    def pulse_sprint_ids(self) -> dict[str, int]:
        return {p.project_key: p.sprint_id for p in self.pulses}

    @property
    def oncall_email(self) -> str | None:
        return self.weekend_oncall[0].engineer_email if self.weekend_oncall else None


_OPERATIONS_ROLES = (Role.PVG, Role.BVG, Role.GEN)


@dataclass
class ChipGroup:
    key: str
    label: str
    local_day: str
    chips: list[ChipVM]

    @property
    def ops_chips(self) -> list[ChipVM]:
        """Operations sub-group: PVG / BVG / GEN."""
        return [c for c in self.chips if c.role in _OPERATIONS_ROLES]

    @property
    def project_chips(self) -> list[ChipVM]:
        """Project sub-group: Project / OFF."""
        return [c for c in self.chips if c.role not in _OPERATIONS_ROLES]


def load_fetch_data(db: Database, fetched_at: datetime, fetch_id: int) -> DashboardData:
    return DashboardData(
        fetched_at=fetched_at,
        tickets=db.get_tickets(fetch_id),
        touches=db.get_touches(fetch_id),
        alerts=db.get_alerts(fetch_id),
        pulses=db.get_pulses(fetch_id),
        weekend_oncall=db.get_weekend_oncall(fetch_id),
    )


def load_merged_data(db: Database, now: datetime) -> DashboardData:
    """Accumulate state across every fetch in the current pulse (#88).

    Each refresh stores an append-only layer, possibly from an incremental Jira
    window. Tickets (latest-wins per id) and touches (union) accumulate so a
    small delta fetch never drops earlier data. Alerts, however, come only from
    the latest successful fetch: PagerDuty is re-fetched in full each refresh, so
    accumulating would just resurface stale, pre-enrichment alerts from old
    fetches (e.g. "ACK — alert" rows with no incident title/number).
    """
    snaps = db.fetches_since(_pulse_start(now))
    if not snaps:
        latest = db.latest_fetch()
        snaps = [latest] if latest is not None else []
    if not snaps:
        return DashboardData(fetched_at=now)

    tickets: dict[str, Ticket] = {}
    touches: dict[tuple, TouchEvent] = {}
    pulses: list[Pulse] = []
    oncall: list[WeekendOnCall] = []
    for snap in snaps:  # oldest → newest, so later layers win
        for t in db.get_tickets(snap.id):
            tickets[t.id] = t
        for tc in db.get_touches(snap.id):
            touches[(tc.ticket_id, tc.engineer_email, tc.kind, tc.at)] = tc
        snap_pulses = db.get_pulses(snap.id)
        if snap_pulses:
            pulses = snap_pulses
        snap_oncall = db.get_weekend_oncall(snap.id)
        if snap_oncall:
            oncall = snap_oncall

    # Alerts: PagerDuty is fetched incrementally (only since the last refresh),
    # so each snapshot holds just its window's alerts — accumulate across every
    # PagerDuty-ok snapshot in the pulse. Dedup by (incident, handler, state,
    # time); the 1h fetch overlap re-emits some events, so prefer the enriched
    # copy (with incident title/number) when the same event recurs.
    alerts_by_key: dict[tuple, Alert] = {}
    for snap in snaps:  # oldest → newest
        if not snap.pagerduty_ok:
            continue
        for a in db.get_alerts(snap.id):
            key = (a.id, a.handler_email, a.state, a.at)
            existing = alerts_by_key.get(key)
            if existing is None or (a.title and not existing.title):
                alerts_by_key[key] = a
    alerts = list(alerts_by_key.values())

    return DashboardData(
        fetched_at=snaps[-1].fetched_at,
        tickets=list(tickets.values()),
        touches=list(touches.values()),
        alerts=alerts,
        pulses=pulses,
        weekend_oncall=oncall,
    )


def resolve_roles(
    db: Database, emails: list[str], timezone: str, now: datetime
) -> dict[str, Role]:
    weekly = db.get_weekly_schedule()
    overrides = db.get_active_overrides(now)
    return {
        email: effective_role(email, timezone, now, weekly, overrides)
        for email in emails
    }


def _pulse_start(now: datetime) -> datetime:
    """Start of the current pulse (anchored Monday) as a UTC datetime (#93)."""
    _, start, _ = current_pulse(now.astimezone(UTC).date())
    return datetime(start.year, start.month, start.day, tzinfo=UTC)


def _touched_since(email: str, data: DashboardData, since: datetime) -> int:
    return len({
        tc.ticket_id for tc in data.touches
        if tc.engineer_email == email and tc.at >= since
    })


def _alerts_since(email: str, data: DashboardData, since: datetime) -> tuple[int, int]:
    ack = resolved = 0
    for a in data.alerts:
        if a.handler_email != email or a.at < since:
            continue
        if a.state is AlertState.ACKNOWLEDGED:
            ack += 1
        elif a.state is AlertState.RESOLVED:
            resolved += 1
    return ack, resolved


def _ticket_time_since(email: str, data: DashboardData, since: datetime) -> int:
    """Seconds of worklog time on tickets assigned to ``email`` since ``since``
    (assignee proxy, #167): worklog touches carry the duration, attributed to the
    ticket's assignee because Tempo logs them under a bot author."""
    return sum(
        tc.seconds for tc in data.touches
        if tc.engineer_email == email and tc.kind is TouchKind.WORKLOG and tc.at >= since
    )


def _alert_time_since(email: str, data: DashboardData, since: datetime) -> int:
    """Seconds spent on alerts ``email`` resolved since ``since`` (#167): sum of
    ack→resolve for each incident this SRE resolved (the resolver), measured from
    the incident's earliest acknowledgement."""
    ack_at: dict[str, datetime] = {}
    resolved: dict[str, tuple[datetime, str]] = {}  # incident → (earliest resolve, resolver)
    for a in data.alerts:
        if a.state is AlertState.ACKNOWLEDGED:
            if a.id not in ack_at or a.at < ack_at[a.id]:
                ack_at[a.id] = a.at
        elif a.state is AlertState.RESOLVED:
            if a.id not in resolved or a.at < resolved[a.id][0]:
                resolved[a.id] = (a.at, a.handler_email)
    total = 0
    for iid, (res_at, resolver) in resolved.items():
        if resolver != email or res_at < since:
            continue
        acked = ack_at.get(iid)
        if acked is not None and res_at >= acked:
            total += int((res_at - acked).total_seconds())
    return total


def _completed_since(email: str, data: DashboardData, since: date) -> int:
    return sum(
        1 for t in data.tickets
        if t.assignee_email == email and t.is_done_date is not None and t.is_done_date >= since
    )


def _assigned_open(email: str, data: DashboardData) -> int:
    """Open assigned work (To Do + WIP) that is in this pulse's scope."""
    psids = data.pulse_sprint_ids
    return sum(
        1 for t in data.tickets
        if t.assignee_email == email and in_scope(t, psids)
        and t.group in (TicketGroup.TODO, TicketGroup.WIP)
    )


def build_chip(
    email: str, role: Role, region_key: str, data: DashboardData, now: datetime
) -> ChipVM:
    eng = config.ENGINEERS_BY_EMAIL[email]
    cutoff = now - _24H
    pstart = _pulse_start(now)
    ack24, res24 = _alerts_since(email, data, cutoff)
    ackp, resp = _alerts_since(email, data, pstart)
    return ChipVM(
        email=email,
        name=eng.name,
        role=role,
        is_manager=eng.is_manager,
        region_key=region_key,
        assigned_open=_assigned_open(email, data),
        touched_24h=_touched_since(email, data, cutoff),
        completed_24h=_completed_since(email, data, cutoff.date()),
        alerts_ack_24h=ack24,
        alerts_resolved_24h=res24,
        touched_pulse=_touched_since(email, data, pstart),
        completed_pulse=_completed_since(email, data, pstart.date()),
        alerts_ack_pulse=ackp,
        alerts_resolved_pulse=resp,
    )


def build_chip_groups(
    db: Database, data: DashboardData, selected_regions: list[str], now: datetime
) -> tuple[list[ChipGroup], list[ChipVM]]:
    """Per-region chip groups + a separate Management group (#72)."""
    groups: list[ChipGroup] = []
    for key in selected_regions:
        region = config.REGIONS[key]
        emails = list(region.member_emails)
        roles = resolve_roles(db, emails, region.timezone, now)
        # On the weekend, every engineer except the on-call is OFF (FR-025).
        if is_weekend(now, region.timezone):
            roles.update(others_off(data.oncall_email, emails))
        local_day = now.astimezone(ZoneInfo(region.timezone)).strftime("%a %d %b")
        chips = [build_chip(e, roles[e], key, data, now) for e in emails]
        groups.append(ChipGroup(key=key, label=key, local_day=local_day, chips=chips))

    # Management (regional + global managers) is shown on its own, excluded from
    # region counts and not tied to any region's daily role schedule (#72).
    management = [e.email for e in config.management_engineers()]
    management_chips = [
        build_chip(e, Role.OFF, "Management", data, now) for e in management
    ]
    return groups, management_chips


def build_counts(
    data: DashboardData, selected_regions: list[str], now: datetime
) -> list[CountsRow]:
    """Counts rows for the selected region(s), combined + deduped (FR-024)."""
    if not selected_regions:
        return []
    return _build_counts(selected_regions, data.tickets, data.alerts, data.pulses, now)


# --- Colour-rule legend (#143) ---------------------------------------------

# One representative assigned, in-flight (non-Done) ticket per category the
# colour rules key on. The legend is rendered by running these through the very
# same coloring functions the dashboard uses, so it can never drift from the
# real behaviour.
_LEGEND_TYPES: tuple[tuple[str, Ticket], ...] = (
    ("ISReq Highest", Ticket("_", "ISReq", "x", "In Progress", PRIORITY_HIGHEST)),
    ("ISReq [PR/MP Review]", Ticket("_", "ISReq", "[PR/MP Review] x", "In Progress", "Medium")),
    ("ISReq ps5-blocker",
     Ticket("_", "ISReq", "x", "In Progress", "Medium", labels=[PS5_BLOCKER_LABELS[0]])),
    ("ISReq regular", Ticket("_", "ISReq", "x", "In Progress", "Medium")),
    ("ISDB", Ticket("_", "ISDB", "x", "In Progress", None)),
)
_LEGEND_ROLES = (Role.PVG, Role.BVG, Role.GEN, Role.PROJECT, Role.OFF)


def build_color_legend() -> dict:
    """Role × ticket-type colour matrix, derived live from the coloring rules (#143).

    Each cell is the colour an *assigned, in-flight* ticket of that type gets for
    that role, plus whether the role reclassifies it into the Distractors group.
    """
    # Handled-alert classification per role (#158), derived live from the same
    # alert_classification() the detail panel uses. PVG is state+age dependent (so
    # three sub-states); the others collapse to a single colour.
    def _alert_states(role: Role) -> list[dict]:
        res, _ = alert_classification(role, resolved=True, recent=False)
        rec, _ = alert_classification(role, resolved=False, recent=True)
        old, _ = alert_classification(role, resolved=False, recent=False)
        if res is rec is old:
            return [{"color": res.value, "label": "open or resolved"}]
        return [
            {"color": res.value, "label": "resolved → Success"},
            {"color": rec.value, "label": "open ≤24h → WIP"},
            {"color": old.value, "label": "open >24h → WIP"},
        ]

    # One combined matrix per role: the five ticket cells + the handled-alert cell.
    rows = []
    for role in _LEGEND_ROLES:
        cells = []
        for _, ticket in _LEGEND_TYPES:
            distractor = is_role_distractor(role, ticket)
            color = ticket_color(
                role, ticket, assigned=True, group=ticket.group, role_distractor=distractor
            )
            cells.append({"color": color.value, "distractor": distractor})
        rows.append({
            "role": role.value, "cells": cells, "alert_states": _alert_states(role),
        })
    alert_rows = [{"role": r["role"], "states": r["alert_states"]} for r in rows]

    # Counts-table alert-cell bands, derived from the same coloring thresholds the
    # tables use (single source of truth). Ack/Total are judged against a "cap"
    # that scales by the row's span and the selected-region count; the resolve
    # rate and MTTR/MTTA means are rates, so they keep fixed thresholds.
    pct = lambda r: f"{int(round(r * 100))}%"  # noqa: E731
    alert_bands = [
        {"col": "Alerts Triggered / Total", "green": "≤ cap",
         "yellow": "cap → 2× cap", "red": "> 2× cap"},
        {"col": "Alerts Ack (vs Triggered)", "green": "shortfall ≤ 1/region",
         "yellow": "≤ 2/region", "red": "more behind"},
        {"col": "Alert Res (resolved ÷ ack)", "green": f"≥ {pct(ALERT_RES_GREEN)}",
         "yellow": f"{pct(ALERT_RES_YELLOW)} → {pct(ALERT_RES_GREEN)}",
         "red": f"< {pct(ALERT_RES_YELLOW)}"},
        {"col": "Alert MTTR (ack → resolve)", "green": f"≤ {format_duration(ALERT_MTTR_GREEN_S)}",
         "yellow": f"≤ {format_duration(ALERT_MTTR_YELLOW_S)}",
         "red": f"> {format_duration(ALERT_MTTR_YELLOW_S)}"},
        {"col": "Alert MTTA (trigger → ack)", "green": f"≤ {format_duration(ALERT_MTTA_GREEN_S)}",
         "yellow": f"≤ {format_duration(ALERT_MTTA_YELLOW_S)}",
         "red": f"> {format_duration(ALERT_MTTA_YELLOW_S)}"},
    ]
    # The "cap" = on-call standard (2 alerts / 12h shift) × the row's shifts ×
    # selected regions. Ack/Total scale with regions; the rates above do not.
    alert_caps = {
        "weekday": ALERT_FATIGUE_WEEKDAY,
        "weekend": ALERT_FATIGUE_WEEKEND,
        "pulse": ALERT_FATIGUE_PULSE,
    }

    return {
        "types": [name for name, _ in _LEGEND_TYPES],
        "rows": rows,
        "alert_rows": alert_rows,
        "alert_bands": alert_bands,
        "alert_caps": alert_caps,
    }


# --- Weekend on-call recap (#145) ------------------------------------------


@dataclass
class WeekendRecap:
    oncall_name: str
    weekend_label: str
    incident_count: int
    resolved: int
    open_acks: int
    mttr_label: str
    incidents: list[dict]


def build_weekend_recap(db: Database, data: DashboardData, now: datetime) -> WeekendRecap | None:
    """What the previous weekend's on-call engineer dealt with (#145).

    Summarises the PagerDuty incidents the on-call engineer handled over their
    weekend: count, resolved vs still-open, mean ack→resolve, and each incident
    (title + link). Returns ``None`` when no on-call is known (no iCal data).

    Loads the weekend's alerts directly from the DB rather than ``data.alerts``,
    because the just-passed weekend can fall in the *previous* pulse (so it isn't
    in the current-pulse merge). An incident counts if the on-call touched it
    within the weekend window; its resolve time is taken whenever it resolved, so
    a resolution that slips just past midnight still reads as resolved.
    """
    if not data.weekend_oncall:
        return None
    oc = data.weekend_oncall[0]
    name = _display_name(oc.engineer_email)
    tz = _handler_zone(oc.engineer_email) or UTC
    # Half-open weekend window [Sat 00:00, Mon 00:00) in the on-call's timezone.
    start = datetime(oc.weekend_start.year, oc.weekend_start.month, oc.weekend_start.day, tzinfo=tz)
    mon = oc.weekend_end + timedelta(days=1)
    end = datetime(mon.year, mon.month, mon.day, tzinfo=tz)

    in_weekend: set[str] = set()
    ack_at: dict[str, datetime] = {}
    res_at: dict[str, datetime] = {}
    meta: dict[str, dict] = {}
    for a in accumulated_alerts_since(db, start.astimezone(UTC)):
        if a.handler_email != oc.engineer_email:
            continue
        if start <= a.at < end:
            in_weekend.add(a.id)          # the on-call touched it during the weekend
        if a.state is AlertState.ACKNOWLEDGED and (a.id not in ack_at or a.at < ack_at[a.id]):
            ack_at[a.id] = a.at
        elif a.state is AlertState.RESOLVED and (a.id not in res_at or a.at < res_at[a.id]):
            res_at[a.id] = a.at
        m = meta.get(a.id)
        if m is None or (a.title and not m["title"]):   # prefer an enriched copy
            meta[a.id] = {"title": a.title, "url": a.url, "number": a.number}

    incidents: list[dict] = []
    mttr_total = mttr_n = 0
    for iid in in_weekend:
        m = meta[iid]
        resolved = iid in res_at
        duration = None
        if resolved and iid in ack_at and res_at[iid] >= ack_at[iid]:
            duration = (res_at[iid] - ack_at[iid]).total_seconds()
            mttr_total += int(duration)
            mttr_n += 1
        incidents.append({
            "number": m["number"],
            "title": m["title"] or "(untitled incident)",
            "url": m["url"],
            "resolved": resolved,
            "duration_label": format_duration(duration),
        })
    # Still-open (acknowledged, unresolved) incidents first, then by number desc.
    incidents.sort(key=lambda i: (i["resolved"], -(i["number"] or 0)))
    resolved_count = sum(1 for i in incidents if i["resolved"])
    return WeekendRecap(
        oncall_name=name,
        weekend_label=f"{oc.weekend_start:%a %d} – {oc.weekend_end:%a %d %b}",
        incident_count=len(incidents),
        resolved=resolved_count,
        open_acks=len(incidents) - resolved_count,
        mttr_label=format_duration(mttr_total / mttr_n) if mttr_n else "—",
        incidents=incidents,
    )


# --- Repeat-offender alerts (#146) -----------------------------------------
# Moved to services/offenders.py (now year-history backed, not pulse-scoped);
# the route calls offenders.build_offenders directly.


def build_pulse_history(
    db: Database, data: DashboardData, selected_regions: list[str], now: datetime
) -> list[PulseHistoryRow]:
    """Growing per-pulse history (#80): stored summaries for past pulses + the
    live current/previous pulse, summed across selected regions. Each cell keeps
    a per-person breakdown for the hover tooltip."""
    per_pulse: dict[int, dict[str, Cell]] = {}
    all_alerts: dict[int, int] = {}   # alerts across ALL regions (region-% denominator)
    all_closed: dict[int, int] = {}   # ISReq closed across ALL regions (closed-% denom)
    all_isdb: dict[int, int] = {}     # ISDB closed across ALL regions (isdb-closed-% denom)

    def _slot(pnum: int) -> dict[str, Cell]:
        return per_pulse.setdefault(pnum, {m: Cell() for m in PULSE_SUMMARY_FIELDS})

    for pnum, region, counts, breakdowns in db.get_pulse_summaries():
        all_alerts[pnum] = all_alerts.get(pnum, 0) + counts.get("alerts_total", 0)
        all_closed[pnum] = all_closed.get(pnum, 0) + counts.get("closed_total", 0)
        all_isdb[pnum] = all_isdb.get(pnum, 0) + counts.get("isdb_closed", 0)
        if region not in selected_regions:
            continue
        slot = _slot(pnum)
        for m in PULSE_SUMMARY_FIELDS:
            slot[m].count += counts.get(m, 0)
            for name, n in (breakdowns.get(m) or {}).items():
                slot[m].breakdown[name] = slot[m].breakdown.get(name, 0) + n

    # Overlay only the current pulse with freshly-computed cells so it reflects
    # the latest in-pulse data. Past pulses (incl. the immediately previous one)
    # come from stored summaries: the live snapshot's window no longer covers
    # them in full — alerts before the PagerDuty floor read as zero — so a live
    # recompute would blank out backfilled history (e.g. Pulse 11's alerts).
    if selected_regions:
        zone = ZoneInfo(config.REGIONS[selected_regions[0]].timezone)
        cur_num, _, _ = current_pulse(now.astimezone(zone).date())
        by_region = {
            r: region_pulse_summary(r, data.tickets, data.alerts, data.pulses, now)
            for r in config.REGION_KEYS
        }
        per_pulse[cur_num] = combine_summaries([by_region[r] for r in selected_regions])
        all_alerts[cur_num] = sum(by_region[r]["alerts_total"].count for r in config.REGION_KEYS)
        all_closed[cur_num] = sum(by_region[r]["closed_total"].count for r in config.REGION_KEYS)
        all_isdb[cur_num] = sum(by_region[r]["isdb_closed"].count for r in config.REGION_KEYS)

    # The pulse-volume green cap scales by the number of selected regions, exactly
    # like the per-day counts table (more on-call engineers ⇒ a higher ceiling).
    pulse_cap = ALERT_FATIGUE_PULSE * max(len(selected_regions), 1)

    rows: list[PulseHistoryRow] = []
    for pnum in sorted(per_pulse):
        cells = per_pulse[pnum]
        ga, gc, gi = all_alerts.get(pnum, 0), all_closed.get(pnum, 0), all_isdb.get(pnum, 0)
        ack_n, res_n = cells["alerts_ack"].count, cells["alerts_resolved"].count
        total_n = cells["alerts_total"].count
        mttr_s = (
            cells["alert_mttr_sum"].count / cells["alert_mttr_n"].count
            if cells["alert_mttr_n"].count else None
        )
        mtta_s = (
            cells["alert_mtta_sum"].count / cells["alert_mtta_n"].count
            if cells["alert_mtta_n"].count else None
        )
        cycle_d = (
            cells["ticket_cycle_sum"].count / cells["ticket_cycle_n"].count
            if cells["ticket_cycle_n"].count else None
        )
        rows.append(PulseHistoryRow(
            pnum, f"Pulse {pnum}", cells=cells,
            region_pct=(100.0 * total_n / ga) if ga else None,
            closed_pct=(100.0 * cells["closed_total"].count / gc) if gc else None,
            isdb_closed_pct=(100.0 * cells["isdb_closed"].count / gi) if gi else None,
            alert_mttr_seconds=mttr_s,
            alert_mtta_seconds=mtta_s,
            ticket_cycle_days=cycle_d,
            triggered_level=count_level(cells["alerts_triggered"].count, pulse_cap),
            ack_level=count_level(ack_n, pulse_cap),
            total_level=count_level(total_n, pulse_cap),
            resolved_level=resolve_rate_level(res_n, ack_n),
            closed_pr_mp_level=pr_mp_review_level(
                cells["new_pr_mp"].count, cells["closed_pr_mp"].count),
            closed_highest_level=closed_vs_new_level(
                cells["closed_highest"].count, cells["new_highest"].count),
            closed_ps5_level=closed_vs_new_level(
                cells["closed_ps5"].count, cells["new_ps5"].count),
            closed_total_level=closed_vs_new_total_level(
                cells["closed_total"].count, cells["new_total"].count,
                max(len(selected_regions), 1)),
        ))

    # Trend colouring vs the previous pulse that had data (rows are in ascending
    # pulse order). Days-to-close: green when closed > new (clearing backlog
    # inflates cycle time) or faster than the previous pulse, red when slower.
    # MTTA/MTTR: always green at or below their healthy floor (5m / 30m), else
    # green faster / red slower vs the previous pulse (#149 follow-up) — distinct
    # from the counts table's fixed thresholds.
    prev_cycle: float | None = None
    prev_mtta: float | None = None
    prev_mttr: float | None = None
    # Intake (New columns): fewer new tickets than the previous pulse is green.
    # Every pulse has an intake count (0 is real), so compare to the immediately
    # previous pulse — no "had data" skip like the alert means above. New Total
    # also gets a healthy floor: the average New Total across *completed* pulses
    # (the current/partial pulse is excluded so it can be judged against the norm);
    # at/below it is green regardless of the pulse-to-pulse change.
    cur_pnum = max((r.pulse_number for r in rows), default=None)
    hist_totals = [r.cells["new_total"].count for r in rows if r.pulse_number != cur_pnum]
    intake_floor = (sum(hist_totals) / len(hist_totals)) if hist_totals else None
    new_cols = ("new_highest", "new_pr_mp", "new_ps5", "new_regular", "new_total")
    prev_new: dict[str, int | None] = {col: None for col in new_cols}
    for row in rows:
        row.cycle_level = cycle_color(
            row.ticket_cycle_days, prev_cycle,
            row.cells["closed_total"].count, row.cells["new_total"].count)
        if row.ticket_cycle_days is not None:
            prev_cycle = row.ticket_cycle_days
        row.mtta_level = mtta_trend_level(row.alert_mtta_seconds, prev_mtta)
        if row.alert_mtta_seconds is not None:
            if prev_mtta is not None:
                row.mtta_delta_seconds = row.alert_mtta_seconds - prev_mtta
            prev_mtta = row.alert_mtta_seconds
        row.mttr_level = mttr_trend_level(row.alert_mttr_seconds, prev_mttr)
        if row.alert_mttr_seconds is not None:
            if prev_mttr is not None:
                row.mttr_delta_seconds = row.alert_mttr_seconds - prev_mttr
            prev_mttr = row.alert_mttr_seconds
        for col in new_cols:
            cur = row.cells[col].count
            floor = intake_floor if col == "new_total" else None
            setattr(row, f"{col}_level", intake_level(cur, prev_new[col], floor))
            prev_new[col] = cur
    return rows


def build_panel(
    db: Database,
    email: str,
    data: DashboardData,
    now: datetime,
    *,
    region_key: str,
    highest_focus: bool = False,
) -> DetailPanelVM:
    eng = config.ENGINEERS_BY_EMAIL[email]
    region = config.REGIONS[region_key]
    role = resolve_roles(db, [email], region.timezone, now)[email]
    # Managers/global management get a simple view of their own work: To Do / WIP
    # / Done, no Distractors and no role-based reclassification (#72 follow-up).
    is_management = eng.is_manager or eng.is_global

    # Sprintless ISDB completions count as Success only if done this pulse, so
    # pass the anchored pulse window (region-local) to the classifier (#ISDB).
    today = now.astimezone(ZoneInfo(region.timezone)).date()
    _, pstart, pend = current_pulse(today)
    grouped = classify_for_engineer(
        email, data.tickets, data.touches, data.pulse_sprint_ids, (pstart, pend)
    )

    # Reclassify assigned tickets into Distractors (To Do / queued work is never a
    # distraction, even when untriaged):
    #  * highest_focus toggle (WIP only): any in-progress ISReq not Highest /
    #    not [PR/MP Review] → red.
    #  * role rules (#86): BVG non-priority, PVG In-Review (yellow), Project
    #    non-ISDB (red) — Project distractions also pull completed work out of
    #    Success, since off-task ISReq is never a success for a Project engineer.
    focus_distractor_ids: set[str] = set()
    role_distractor_ids: set[str] = set()
    scan_groups = () if is_management else (TicketGroup.WIP, TicketGroup.SUCCESS)
    for grp in scan_groups:
        # The Highest-focus toggle is about in-progress focus; it never demotes a
        # completed ticket, so it applies to WIP only.
        apply_focus = highest_focus and grp is TicketGroup.WIP
        kept = []
        for t in grouped[grp]:
            role_dist = is_role_distractor(role, t)
            if role_dist and role is Role.PVG:
                # PVG's "In Review" rule wins over the Highest-only toggle (yellow).
                grouped[TicketGroup.DISTRACTORS].append(t)
                role_distractor_ids.add(t.id)
            elif apply_focus and t.is_isreq and not (t.is_highest or t.is_pr_mp_review):
                grouped[TicketGroup.DISTRACTORS].append(t)
                focus_distractor_ids.add(t.id)
            elif role_dist:
                grouped[TicketGroup.DISTRACTORS].append(t)
                role_distractor_ids.add(t.id)
            else:
                kept.append(t)
        grouped[grp] = kept

    touched_24h_ids = {
        tc.ticket_id for tc in data.touches
        if tc.engineer_email == email and tc.at >= now - _24H
    }
    # A Highest ticket still open is flagged with how many full pulses it has
    # stayed open (#18); 1 pulse = PULSE_LENGTH_DAYS days. 0 = fresh / not Highest.
    def _pulses_open(t: Ticket, group: TicketGroup) -> int:
        if not (
            t.is_highest
            and group in (TicketGroup.TODO, TicketGroup.WIP)
            and t.created is not None
        ):
            return 0
        return (now - t.created).days // config.PULSE_LENGTH_DAYS

    shown = (TicketGroup.TODO, TicketGroup.WIP, TicketGroup.SUCCESS)
    if not is_management:
        shown = (*shown, TicketGroup.DISTRACTORS)
    out: dict[str, list[TicketVM]] = {}
    for group in shown:
        vms: list[TicketVM] = []
        for t in grouped[group]:
            if t.id in focus_distractor_ids:
                color = Color.RED
            else:
                is_rd = t.id in role_distractor_ids
                assigned = group is not TicketGroup.DISTRACTORS or is_rd
                color = ticket_color(
                    role, t, assigned=assigned, group=group, role_distractor=is_rd
                )
            vms.append(
                TicketVM(
                    key=t.id,
                    title=t.title,
                    color=color,
                    is_bvg_review=t.is_bvg_review,
                    url=config.jira_browse_url(t.id),
                    touched_24h=t.id in touched_24h_ids,
                    pulses_open=_pulses_open(t, group),
                    status=t.status,
                )
            )
        out[group.value] = vms

    # Surface the engineer's own alerts. Dedupe by incident (resolved wins), then
    # classify per the final matrix (#158): PVG green-resolved / yellow-open≤24h /
    # red-open>24h, BVG yellow, GEN/Project/OFF red distraction. Many alert events
    # lack a title/link because they were captured by an early un-enriched fetch,
    # so back-fill title/number/link from the stored incident table (#157).
    alert_by_incident: dict[str, Alert] = {}
    for a in data.alerts:
        if a.handler_email != email:
            continue
        prev = alert_by_incident.get(a.id)
        if prev is None or prev.state is not AlertState.RESOLVED:
            alert_by_incident[a.id] = a
    meta = db.incident_meta(alert_by_incident.keys())
    for a in sorted(alert_by_incident.values(),
                    key=lambda x: (x.title or meta.get(x.id, (None,))[0] or x.id).lower()):
        recent = a.at >= now - _24H
        resolved = a.state is AlertState.RESOLVED
        m_title, m_number, m_url = meta.get(a.id, (None, None, None))
        title = a.title or m_title
        number = a.number if a.number is not None else m_number
        url = a.url or m_url
        if is_management:
            color = Color.GREEN if resolved else Color.YELLOW
            target = TicketGroup.SUCCESS if resolved else TicketGroup.WIP
        else:
            color, target = alert_classification(role, resolved=resolved, recent=recent)
        # Line: "STATUS — #code — Title" (code = PagerDuty incident number).
        parts = ["RES" if resolved else "ACK"]
        if number is not None:
            parts.append(f"#{number}")
        parts.append(title or "alert")
        vm = TicketVM(
            key="⚠",
            title=" — ".join(parts),
            color=color,
            url=url,
            touched_24h=recent,
        )
        out[target.value].append(vm)

    pulse_start = _pulse_start(now)
    return DetailPanelVM(
        email=email, name=eng.name, role=role, groups=out,
        alert_time_seconds=_alert_time_since(email, data, pulse_start),
        ticket_time_seconds=_ticket_time_since(email, data, pulse_start),
    )
