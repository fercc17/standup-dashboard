"""Presentation view models built from stored fetch data (FR-018/019) — T026.

Pure-ish glue: loads a fetch layer from SQLite, resolves each engineer's
effective role in their region timezone, and assembles chips + detail panels,
applying the tested color matrix. Multi-region grouping/dedup (US4) and the
counts table (US3) extend this module in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.coloring import ticket_color
from ..domain.models import (
    Alert,
    AlertState,
    ChipVM,
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
from ..services.classification import classify_for_engineer
from ..services.counts import build_counts as _build_counts
from ..services.oncall import others_off
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


@dataclass
class ChipGroup:
    key: str
    label: str
    local_day: str
    chips: list[ChipVM]


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


def _touched_24h(email: str, data: DashboardData, now: datetime) -> int:
    cutoff = now - _24H
    return len({
        tc.ticket_id for tc in data.touches
        if tc.engineer_email == email and tc.at >= cutoff
    })


def _alerts_24h(email: str, data: DashboardData, now: datetime) -> tuple[int, int]:
    cutoff = now - _24H
    ack = resolved = 0
    for a in data.alerts:
        if a.handler_email != email or a.at < cutoff:
            continue
        if a.state is AlertState.ACKNOWLEDGED:
            ack += 1
        elif a.state is AlertState.RESOLVED:
            resolved += 1
    return ack, resolved


def build_chip(
    email: str, role: Role, region_key: str, data: DashboardData, now: datetime
) -> ChipVM:
    eng = config.ENGINEERS_BY_EMAIL[email]
    ack, resolved = _alerts_24h(email, data, now)
    return ChipVM(
        email=email,
        name=eng.name,
        role=role,
        is_manager=eng.is_manager,
        touched_24h=_touched_24h(email, data, now),
        alerts_ack_24h=ack,
        alerts_resolved_24h=resolved,
        region_key=region_key,
    )


def build_chip_groups(
    db: Database, data: DashboardData, selected_regions: list[str], now: datetime
) -> tuple[list[ChipGroup], list[ChipVM]]:
    """Per-region chip groups (manager once per owned region) + Global group."""
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

    globals_ = [e.email for e in config.global_engineers()]
    global_chips = [
        build_chip(e, Role.OFF, "Global", data, now) for e in globals_
    ]
    return groups, global_chips


def build_counts(
    data: DashboardData, selected_regions: list[str], now: datetime
) -> list[CountsRow]:
    """Counts rows for the selected region(s), combined + deduped (FR-024)."""
    if not selected_regions:
        return []
    return _build_counts(selected_regions, data.tickets, data.alerts, data.pulses, now)


def any_bvg_today(db: Database, selected_regions: list[str], now: datetime) -> bool:
    """True iff any engineer in the selected regions is BVG today (FR-010/032)."""
    for key in selected_regions:
        region = config.REGIONS[key]
        roles = resolve_roles(db, list(region.member_emails), region.timezone, now)
        if any(r is Role.BVG for r in roles.values()):
            return True
    return False


def build_panel(
    db: Database,
    email: str,
    data: DashboardData,
    now: datetime,
    *,
    region_key: str,
    strict_mode: bool,
) -> DetailPanelVM:
    eng = config.ENGINEERS_BY_EMAIL[email]
    region = config.REGIONS[region_key]
    role = resolve_roles(db, [email], region.timezone, now)[email]

    grouped = classify_for_engineer(email, data.tickets, data.touches, data.pulse_sprint_ids)
    out: dict[str, list[TicketVM]] = {}
    for group in (TicketGroup.TODO, TicketGroup.WIP, TicketGroup.SUCCESS, TicketGroup.DISTRACTORS):
        assigned = group is not TicketGroup.DISTRACTORS
        out[group.value] = [
            TicketVM(
                key=t.id,
                title=t.title,
                color=ticket_color(
                    role, t, assigned=assigned, strict_mode=strict_mode, group=group
                ),
                is_bvg_review=t.is_bvg_review,
            )
            for t in grouped[group]
        ]
    return DetailPanelVM(email=email, name=eng.name, role=role, groups=out)
