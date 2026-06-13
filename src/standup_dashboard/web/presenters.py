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
from ..domain.coloring import is_role_distractor, ticket_color
from ..domain.models import (
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
    WeekendOnCall,
)
from ..domain.roles import effective_role, is_weekend
from ..services.classification import classify_for_engineer, in_scope
from ..services.counts import ALERT_FATIGUE_PULSE, combine_summaries, region_pulse_summary
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

    rows: list[PulseHistoryRow] = []
    for pnum in sorted(per_pulse):
        cells = per_pulse[pnum]
        ga, gc, gi = all_alerts.get(pnum, 0), all_closed.get(pnum, 0), all_isdb.get(pnum, 0)
        rows.append(PulseHistoryRow(
            pnum, f"Pulse {pnum}", cells=cells,
            region_pct=(100.0 * cells["alerts_total"].count / ga) if ga else None,
            closed_pct=(100.0 * cells["closed_total"].count / gc) if gc else None,
            isdb_closed_pct=(100.0 * cells["isdb_closed"].count / gi) if gi else None,
            alert_fatigue=cells["alerts_total"].count > ALERT_FATIGUE_PULSE,
            alert_mttr_seconds=(
                cells["alert_mttr_sum"].count / cells["alert_mttr_n"].count
                if cells["alert_mttr_n"].count else None
            ),
        ))
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

    # Surface the engineer's own alerts: resolved → green under Success,
    # acknowledged → yellow under WIP. Dedupe by incident (resolved wins), show
    # the incident title + a PagerDuty link, sorted alphabetically.
    alert_by_incident: dict[str, Alert] = {}
    for a in data.alerts:
        if a.handler_email != email:
            continue
        prev = alert_by_incident.get(a.id)
        if prev is None or prev.state is not AlertState.RESOLVED:
            alert_by_incident[a.id] = a
    # For GEN, alerts are a distraction (their focus is ISReq tickets): all alerts
    # go under Distractors — unresolved acks red, resolved yellow.
    gen_alerts = role is Role.GEN and not is_management
    for a in sorted(alert_by_incident.values(), key=lambda x: (x.title or x.id).lower()):
        recent = a.at >= now - _24H
        resolved = a.state is AlertState.RESOLVED
        if gen_alerts:
            color = Color.YELLOW if resolved else Color.RED
            target = TicketGroup.DISTRACTORS
        elif resolved:
            color, target = Color.GREEN, TicketGroup.SUCCESS
        elif recent:
            color, target = Color.YELLOW, TicketGroup.WIP   # acked in the last 24h
        else:
            color, target = Color.RED, TicketGroup.WIP      # acked >24h, still open → stale
        # Line: "STATUS — #code — Title" (code = PagerDuty incident number).
        parts = ["RES" if resolved else "ACK"]
        if a.number is not None:
            parts.append(f"#{a.number}")
        parts.append(a.title or "alert")
        vm = TicketVM(
            key="⚠",
            title=" — ".join(parts),
            color=color,
            url=a.url,
            touched_24h=recent,
        )
        out[target.value].append(vm)

    return DetailPanelVM(email=email, name=eng.name, role=role, groups=out)
