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
    Alert,
    AlertState,
    ChipVM,
    Color,
    CountsRow,
    DetailPanelVM,
    Pulse,
    Role,
    Ticket,
    TicketGroup,
    TicketVM,
    TouchEvent,
    WeekendOnCall,
)
from ..domain.roles import effective_role, is_weekend
from ..services.classification import classify_for_engineer, in_scope
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

    grouped = classify_for_engineer(email, data.tickets, data.touches, data.pulse_sprint_ids)

    # Reclassify assigned *in-progress* tickets into Distractors (To Do / queued
    # work is never a distraction, even when untriaged):
    #  * highest_focus toggle: any ISReq not Highest / not [PR/MP Review] → red.
    #  * role rules (#86): BVG non-priority, Project non-ISDB (Project flagged yellow).
    focus_distractor_ids: set[str] = set()
    role_distractor_ids: set[str] = set()
    for grp in () if is_management else (TicketGroup.WIP,):
        kept = []
        for t in grouped[grp]:
            if highest_focus and t.is_isreq and not (t.is_highest or t.is_pr_mp_review):
                grouped[TicketGroup.DISTRACTORS].append(t)
                focus_distractor_ids.add(t.id)
            elif is_role_distractor(role, t):
                grouped[TicketGroup.DISTRACTORS].append(t)
                role_distractor_ids.add(t.id)
            else:
                kept.append(t)
        grouped[grp] = kept

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
                )
            )
        out[group.value] = vms

    # Surface the engineer's own alerts (we already have them): resolved → green
    # under Success, acknowledged → yellow under WIP. Dedupe by incident.
    state_by_incident: dict[str, AlertState] = {}
    for a in data.alerts:
        if a.handler_email != email:
            continue
        if state_by_incident.get(a.id) is not AlertState.RESOLVED:
            state_by_incident[a.id] = a.state
    for incident_id, state in sorted(state_by_incident.items()):
        if state is AlertState.RESOLVED:
            out[TicketGroup.SUCCESS.value].append(
                TicketVM(key=f"⚠ {incident_id}", title="alert — resolved", color=Color.GREEN)
            )
        else:
            out[TicketGroup.WIP.value].append(
                TicketVM(key=f"⚠ {incident_id}", title="alert — acknowledged", color=Color.YELLOW)
            )

    return DetailPanelVM(email=email, name=eng.name, role=role, groups=out)
