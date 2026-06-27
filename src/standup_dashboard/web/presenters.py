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
    CalendarAvail,
    Cell,
    ChipVM,
    Color,
    CountsRow,
    DetailPanelVM,
    GitHubPRStats,
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
    hours_label,
)
from ..domain.roles import (
    DEFAULT_WEEKDAY_ROLE,
    effective_role,
    is_weekend,
    region_weekday,
)
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
    # email → PR stats (#173): per-pulse, plus the last-24h and today subsets
    github_prs: dict[str, GitHubPRStats] = field(default_factory=dict)
    github_prs_24h: dict[str, GitHubPRStats] = field(default_factory=dict)
    github_prs_today: dict[str, GitHubPRStats] = field(default_factory=dict)
    # email → calendar busy/open this pulse + today + rolling-24h (#cal)
    calendar: dict[str, CalendarAvail] = field(default_factory=dict)
    # Live open-work summary counts from the latest fetch (#summary-live): metric key
    # → count, straight from the Jira filters / PagerDuty the summary links to. Empty
    # keys fall back to a local tally in build_open_summary.
    open_counts: dict[str, int] = field(default_factory=dict)

    @property
    def active_sprint_ids(self) -> set[int]:
        """Every active sprint making up the current pulse. A board can run its
        own sprint plus a shared cross-team one, so a ticket is this-pulse work
        when it belongs to any of them (see ``classification.in_pulse``)."""
        return {p.sprint_id for p in self.pulses}

    @property
    def oncall_email(self) -> str | None:
        """Current / just-passed weekend on-call (earliest stored weekend) — drives
        weekend role assignment and the recap."""
        if not self.weekend_oncall:
            return None
        return min(self.weekend_oncall, key=lambda w: w.weekend_start).engineer_email

    @property
    def next_oncall_email(self) -> str | None:
        """The upcoming weekend's on-call (latest stored weekend), for the header."""
        if not self.weekend_oncall:
            return None
        return max(self.weekend_oncall, key=lambda w: w.weekend_start).engineer_email


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
    pulse_start = _pulse_start(now)
    snaps = db.fetches_since(pulse_start)
    if not snaps:
        latest = db.latest_fetch()
        snaps = [latest] if latest is not None else []
    if not snaps:
        return DashboardData(fetched_at=now)

    tickets: dict[str, Ticket] = {}
    touches: dict[tuple, TouchEvent] = {}
    pulses: list[Pulse] = []
    oncall: list[WeekendOnCall] = []
    github_prs: dict[str, GitHubPRStats] = {}
    github_prs_24h: dict[str, GitHubPRStats] = {}
    github_prs_today: dict[str, GitHubPRStats] = {}
    calendar: dict[str, CalendarAvail] = {}
    open_counts: dict[str, int] = {}
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
        # PR counts are a current snapshot (not accumulated): latest wins.
        snap_prs, snap_prs_24h, snap_prs_today = db.get_github_prs(snap.id)
        if snap_prs:
            github_prs = snap_prs
            github_prs_24h = snap_prs_24h
            github_prs_today = snap_prs_today
        # Open-work summary counts are a live current snapshot too: latest fetch that
        # has them wins, merged per-key so a transient miss keeps the last good value.
        open_counts.update(db.get_open_summary(snap.id))
        # Calendar busy/open accumulates per engineer (latest fetch that has each
        # email wins). A public iCal feed occasionally times out for one person on
        # a given refresh; merging per-email means that transient miss keeps their
        # last-good value instead of blanking them until the next clean fetch.
        for email, av in db.get_calendar_avail(snap.id).items():
            calendar[email] = av

    # Alerts: PagerDuty is fetched incrementally (only since the last refresh),
    # so each snapshot holds just its window's alerts — accumulate across every
    # PagerDuty-ok snapshot in the pulse. Dedup by (incident, handler, state,
    # time); the 1h fetch overlap re-emits some events, so prefer the enriched
    # copy (with incident title/number) when the same event recurs.
    #
    # Scope by the alert's own timestamp: a resolved incident whose events predate
    # the pulse is a prior pulse's closed work and must not resurface on this
    # pulse's cards (#stale-prev-pulse). An incident still acked-but-never-resolved,
    # though, is ongoing work — keep its events even when they predate the pulse so
    # an open alert stays visible across the rollover until it resolves
    # (#open-alert-persist; the fetch recheck keeps re-polling it).
    pd_events: list[Alert] = []
    for snap in snaps:  # oldest → newest
        if snap.pagerduty_ok:
            pd_events.extend(db.get_alerts(snap.id))
    resolved_ids = {a.id for a in pd_events if a.state is AlertState.RESOLVED}
    alerts_by_key: dict[tuple, Alert] = {}
    for a in pd_events:
        if a.at < pulse_start and a.id in resolved_ids:
            continue  # prior-pulse closed alert — drop (#stale-prev-pulse)
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
        github_prs=github_prs,
        github_prs_24h=github_prs_24h,
        github_prs_today=github_prs_today,
        calendar=calendar,
        open_counts=open_counts,
    )


def resolve_roles(
    db: Database, emails: list[str], timezone: str, now: datetime,
    pto_today: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Role]:
    """Effective role per engineer. An engineer whose calendar marks today as a day
    off (``pto_today``) resolves to OFF — overriding their weekly schedule — but a
    manual today-only override still wins over the calendar (#cal-off)."""
    weekly = db.get_weekly_schedule()
    overrides = db.get_active_overrides(now)
    out: dict[str, Role] = {}
    for email in emails:
        if email not in overrides and email in pto_today:
            out[email] = Role.OFF
        else:
            out[email] = effective_role(email, timezone, now, weekly, overrides)
    return out


def _pto_today(emails: list[str], data: DashboardData, timezone: str, now: datetime) -> set[str]:
    """Emails whose calendar marks *today* (region-local) as a day off (#cal-off).

    Matched against ``CalendarAvail.pto_days`` (the ``"%a %b %d"`` strings the card
    lists), which already covers a ≥24h day-off block clipped to the local weekday."""
    today = now.astimezone(ZoneInfo(timezone)).strftime("%a %b %d")
    return {
        e for e in emails
        if today in (data.calendar.get(e) or CalendarAvail()).pto_days
    }


def _coverage_role(
    email: str, timezone: str, weekly: dict[tuple[str, str], str],
    weekend_oncall: list[WeekendOnCall], when: datetime,
) -> Role:
    """The role this engineer actually held on the region-local day of ``when``.

    Used to classify work an OFF-today engineer did *earlier in the week* by the
    assignment they had then, not by today's OFF (#off-distractor). Weekends resolve
    to PVG if they were the stored weekend on-call; weekdays use the weekly schedule
    (no overrides — those are today-only and long expired for a past day)."""
    if is_weekend(when, timezone):
        d = when.astimezone(ZoneInfo(timezone)).date()
        on_call = any(
            w.engineer_email == email and w.weekend_start <= d <= w.weekend_end
            for w in weekend_oncall
        )
        return Role.PVG if on_call else Role.OFF
    role_str = weekly.get((email, region_weekday(when, timezone)))
    return Role(role_str) if role_str else DEFAULT_WEEKDAY_ROLE


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


def _project_of(ticket_id: str) -> str:
    """Canonical project key for a Jira key (``ISDB-3341`` → ``ISDB``)."""
    prefix = ticket_id.split("-", 1)[0]
    for key in config.PROJECT_KEYS:
        if key.upper() == prefix.upper():
            return key
    return prefix


def _ticket_time_since(
    email: str, data: DashboardData, since: datetime, project: str | None = None
) -> int:
    """Seconds of worklog time credited to ``email`` since ``since``.

    Worklog touches carry the duration. With a Tempo token they're credited to the
    real logger (#tempo-worklogs); without one they fall back to the ticket's
    assignee, since Jira authors Tempo worklogs under a bot (#167). ``project``
    restricts the sum to one Jira project (ISDB vs ISReq, #173); None = all."""
    return sum(
        tc.seconds for tc in data.touches
        if tc.engineer_email == email and tc.kind is TouchKind.WORKLOG and tc.at >= since
        and (project is None or _project_of(tc.ticket_id) == project)
    )


def _worklog_on_since(
    email: str, data: DashboardData, since: datetime, ticket_ids: set[str]
) -> int:
    """Worklog seconds ``email`` logged since ``since`` on a specific ticket set —
    used to total distractor time (the engineer's worklog on their distractor
    tickets) so it can be shown as a share of their open, non-busy time (#distract-share)."""
    return sum(
        tc.seconds for tc in data.touches
        if tc.engineer_email == email and tc.kind is TouchKind.WORKLOG and tc.at >= since
        and tc.ticket_id in ticket_ids
    )


def _alert_intervals_since(
    email: str, data: DashboardData, since: datetime,
    incident_ids: set[str] | None = None,
) -> list[tuple[datetime, datetime]]:
    """``(ack, resolve)`` spans for incidents ``email`` resolved since ``since``.

    Each incident contributes one span, from its earliest acknowledgement to its
    earliest resolution, credited to the resolver (the same scope as the alert
    counts). Shared by the overlap (sum) and no-overlap (union) time metrics.
    ``incident_ids`` restricts to a given set (e.g. distractor alerts); None = all.
    """
    ack_at: dict[str, datetime] = {}
    resolved: dict[str, tuple[datetime, str]] = {}  # incident → (earliest resolve, resolver)
    for a in data.alerts:
        if a.state is AlertState.ACKNOWLEDGED:
            if a.id not in ack_at or a.at < ack_at[a.id]:
                ack_at[a.id] = a.at
        elif a.state is AlertState.RESOLVED:
            if a.id not in resolved or a.at < resolved[a.id][0]:
                resolved[a.id] = (a.at, a.handler_email)
    spans: list[tuple[datetime, datetime]] = []
    for iid, (res_at, resolver) in resolved.items():
        if resolver != email or res_at < since:
            continue
        if incident_ids is not None and iid not in incident_ids:
            continue
        acked = ack_at.get(iid)
        if acked is not None and res_at >= acked:
            spans.append((acked, res_at))
    return spans


def _alert_time_since(email: str, data: DashboardData, since: datetime) -> int:
    """Alert time summed per incident (#167) — concurrent incidents are counted
    in each of their spans, so this can exceed the wall-clock total."""
    return sum(
        int((res - ack).total_seconds())
        for ack, res in _alert_intervals_since(email, data, since)
    )


def _alert_union_time_since(
    email: str, data: DashboardData, since: datetime,
    incident_ids: set[str] | None = None,
) -> int:
    """Alert time with overlapping incident spans merged into wall-clock (#173):
    when two incidents are handled at once, their shared time is counted once.
    ``incident_ids`` restricts to a subset (e.g. distractor alerts); None = all."""
    spans = sorted(_alert_intervals_since(email, data, since, incident_ids))
    total = 0
    cur_start: datetime | None = None
    cur_end: datetime | None = None
    for ack, res in spans:
        if cur_end is None or ack > cur_end:
            if cur_end is not None:
                total += int((cur_end - cur_start).total_seconds())
            cur_start, cur_end = ack, res
        elif res > cur_end:
            cur_end = res
    if cur_end is not None:
        total += int((cur_end - cur_start).total_seconds())
    return total


def _alert_spans_by_incident(
    alerts: list[Alert],
) -> tuple[dict[str, datetime], dict[str, datetime], dict[str, datetime]]:
    """Earliest fire / ack / resolve time per incident, across all handlers
    (#line-time). Drives each alert row's "lasted / open for" duration."""
    trig: dict[str, datetime] = {}
    ack: dict[str, datetime] = {}
    res: dict[str, datetime] = {}
    buckets = {AlertState.TRIGGERED: trig, AlertState.ACKNOWLEDGED: ack,
               AlertState.RESOLVED: res}
    for a in alerts:
        bucket = buckets.get(a.state)
        if bucket is not None and (a.id not in bucket or a.at < bucket[a.id]):
            bucket[a.id] = a.at
    return trig, ack, res


def _alert_line_time(
    iid: str, now: datetime,
    trig: dict[str, datetime], ack: dict[str, datetime], res: dict[str, datetime],
) -> tuple[str, str]:
    """(label, tooltip) for one alert row: how long it lasted, or has been open.

    Start of life is the incident's fire time, falling back to its first ack when
    the trigger event fell outside the accumulated window. A resolved incident
    reads fire→resolve ("lasted"); a still-open one reads fire→now ("open for").
    """
    start = trig.get(iid) or ack.get(iid)
    if start is None:
        return "", ""
    end = res.get(iid)
    if end is not None:
        return (format_duration(max(0.0, (end - start).total_seconds())),
                "How long the alert lasted (fire → resolve)")
    return (format_duration(max(0.0, (now - start).total_seconds())),
            "How long the alert has been open (fire → now)")


def _completed_since(email: str, data: DashboardData, since: date) -> int:
    return sum(
        1 for t in data.tickets
        if t.assignee_email == email and t.is_done_date is not None and t.is_done_date >= since
    )


def _assigned_open(email: str, data: DashboardData) -> int:
    """Open assigned work (To Do + WIP) that is in this pulse's scope."""
    active = data.active_sprint_ids
    return sum(
        1 for t in data.tickets
        if t.assignee_email == email and in_scope(t, active)
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
        starred=eng.starred,
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


def _region_roles(db: Database, region_key: str, data: DashboardData,
                  now: datetime) -> dict[str, Role]:
    """Effective role per member of one region, applying the weekend on-call rule.

    On the region-local weekend everyone is OFF except the on-call, who covers as
    PVG (FR-025). PVG (rather than OFF) lets the on-call's chip carry a hand-over
    line so they can see who picks the duty up next, e.g. APAC on Monday (#handover)."""
    region = config.REGIONS[region_key]
    emails = list(region.member_emails)
    roles = resolve_roles(db, emails, region.timezone, now,
                          _pto_today(emails, data, region.timezone, now))
    if is_weekend(now, region.timezone):
        roles.update(others_off(data.oncall_email, emails))
        if data.oncall_email in roles:
            roles[data.oncall_email] = Role.PVG
    return roles


def _effort_badge(ticket: Ticket) -> tuple[str, str, bool]:
    """An ISDB ticket's estimate-vs-invested badge: ``(label, tooltip, over_budget)``.

    Shows the original estimate and the total time logged (Jira time tracking, not
    just this pulse) on ISDB lines (#isdb-estimate). Empty when the ticket isn't ISDB
    or carries no time data. ``over_budget`` is set when invested exceeds the estimate.
    """
    if not ticket.is_isdb:
        return "", "", False
    est, spent = ticket.estimate_seconds, ticket.spent_seconds
    if not est and not spent:
        return "", "", False
    invested = format_duration(spent) if spent else "0m"
    if est:
        estimate = format_duration(est)
        pct = round((spent or 0) / est * 100)        # invested as % of the estimate
        over = bool(spent and spent > est)
        suffix = " — over estimate" if over else ""
        return (f"{estimate} ▸ {invested} · {pct}%",
                f"{invested} invested of {estimate} estimate ({pct}%){suffix}", over)
    # Time logged but no estimate set — still useful to surface (e.g. ISDB-3525).
    return f"▸ {invested}", f"{invested} invested (no estimate set)", False


def _next_week_role(weekly: dict[tuple[str, str], str], email: str) -> Role:
    """The role this engineer is scheduled to hold next week — their Monday slot,
    defaulting to GEN. Shown on weekend chips so the upcoming week's assignments are
    visible instead of a wall of OFF (#weekend-preview)."""
    role_str = weekly.get((email, "MON"))
    return Role(role_str) if role_str else DEFAULT_WEEKDAY_ROLE


def _display_roles(db: Database, region_key: str, data: DashboardData,
                   now: datetime) -> dict[str, Role]:
    """Roles as shown to users for one region: the actual effective roles, except on
    the region-local weekend, where non-on-call members preview their next-week
    (Monday) role instead of OFF and the on-call stays PVG (#weekend-preview).

    Shared by the chips and the hand-over map so the weekend on-call can see the
    incoming PVG (e.g. APAC's Monday cover) for the *whole* weekend — not only once
    that region has itself rolled into Monday and stopped reading its WEEKEND slot."""
    actual = _region_roles(db, region_key, data, now)
    region = config.REGIONS[region_key]
    if not is_weekend(now, region.timezone):
        return actual
    weekly = db.get_weekly_schedule()
    return {
        e: (actual[e] if e == data.oncall_email else _next_week_role(weekly, e))
        for e in region.member_emails
    }


def _handover_map(db: Database, data: DashboardData, now: datetime) -> dict[tuple[str, Role], str]:
    """(region, PVG/BVG) → comma-joined holder names, across *all* regions.

    Needed even for unselected regions so a PVG/BVG chip can name its hand-over
    counterpart in the next/previous region (#handover). Uses the *display* roles so a
    region still in its own weekend contributes its incoming Monday PVG/BVG, letting
    the weekend on-call see who picks up next all weekend long (#weekend-preview)."""
    holders: dict[tuple[str, Role], list[str]] = {}
    for key in config.REGION_KEYS:
        for email, role in _display_roles(db, key, data, now).items():
            if role in (Role.PVG, Role.BVG):
                holders.setdefault((key, role), []).append(
                    config.ENGINEERS_BY_EMAIL[email].name)
    return {k: ", ".join(v) for k, v in holders.items()}


def _handover_region(region_key: str, offset: int) -> str:
    """Region ``offset`` steps along the APAC→EMEA→AMER cycle, '' if off-cycle."""
    order = config.HANDOVER_ORDER
    if region_key not in order:
        return ""
    return order[(order.index(region_key) + offset) % len(order)]


def _handover_name(holders: dict[tuple[str, Role], str], region_key: str,
                   role: Role, offset: int) -> str:
    """Holder name in the region ``offset`` steps along the APAC→EMEA→AMER cycle
    (empty when that region has no current holder of ``role``)."""
    other = _handover_region(region_key, offset)
    return holders.get((other, role), "") if other else ""


def build_chip_groups(
    db: Database, data: DashboardData, selected_regions: list[str], now: datetime
) -> tuple[list[ChipGroup], list[ChipVM]]:
    """Per-region chip groups + a separate Management group (#72)."""
    groups: list[ChipGroup] = []
    handover = _handover_map(db, data, now)
    for key in selected_regions:
        region = config.REGIONS[key]
        emails = list(region.member_emails)
        actual_roles = _region_roles(db, key, data, now)
        # Chips show display roles: actual, except a weekend region previews each
        # member's next-week Monday role (on-call stays PVG) (#weekend-preview).
        display_roles = _display_roles(db, key, data, now)
        local_day = now.astimezone(ZoneInfo(region.timezone)).strftime("%a %d %b")
        chips = []
        for e in emails:
            chip = build_chip(e, display_roles[e], key, data, now)
            # Stamp the hand-over line on the genuine PVG/BVG duty-holder, keyed by the
            # *actual* role so a preview chip never gets a spurious line. Carry the
            # counterpart region too, so the rotation stays visible even when that
            # region has nobody on the duty yet (name = "" → "unassigned").
            actual = actual_roles[e]
            if actual in (Role.PVG, Role.BVG):
                chip.handover_to_region = _handover_region(key, +1)
                chip.handover_from_region = _handover_region(key, -1)
                chip.handover_to = handover.get((chip.handover_to_region, actual), "")
                chip.handover_from = handover.get((chip.handover_from_region, actual), "")
            chips.append(chip)
        groups.append(ChipGroup(key=key, label=key, local_day=local_day, chips=chips))

    # Management (regional + global managers) is shown on its own, excluded from
    # region counts and not tied to any region's daily role schedule (#72). They
    # hold no coverage slot, so they're always shown as GEN rather than OFF.
    management = [e.email for e in config.management_engineers()]
    management_chips = [
        build_chip(e, Role.GEN, "Management", data, now) for e in management
    ]
    return groups, management_chips


def build_counts(
    data: DashboardData, selected_regions: list[str], now: datetime
) -> list[CountsRow]:
    """Counts rows for the selected region(s), combined + deduped (FR-024)."""
    if not selected_regions:
        return []
    return _build_counts(selected_regions, data.tickets, data.alerts, data.pulses, now)


# --- Open-work summary line (#summary) -------------------------------------


@dataclass
class OpenSummary:
    """Team-wide "what's still open right now" line shown above the regions.

    Recomputed from the merged pulse data on every refresh: counts of in-scope
    open (To Do + WIP) tickets that are Highest / ps5-blocker / PR-MP review, and
    the number of alerts acknowledged but not yet resolved (still-open incidents).
    """
    highest: int = 0
    ps5: int = 0
    ps5_highest: int = 0
    pr_mp: int = 0
    escalated: int = 0
    ongoing_alerts: int = 0
    # Deep links to the live source of each count (#summary-links): a saved Jira
    # filter per ticket category, and the PagerDuty open-incident list for alerts.
    highest_url: str = ""
    ps5_url: str = ""
    ps5_highest_url: str = ""
    pr_mp_url: str = ""
    escalated_url: str = ""
    alerts_url: str = ""


def build_open_summary(data: DashboardData) -> OpenSummary:
    """Open Highest / ps5-blocker / PR-MP counts + ongoing alerts (#summary).

    Each number comes from the live Jira filter / JQL / PagerDuty count captured at
    fetch time (#summary-live), so it equals the report its link opens. Only if a live
    count is missing (a count query failed, or a pre-#summary-live snapshot) does it
    fall back to a local tally — which is sprint-scoped and so can diverge from the
    report, the very mismatch the live counts fix.
    """
    live = data.open_counts
    active = data.active_sprint_ids
    open_tickets = [
        t for t in data.tickets
        if in_scope(t, active) and t.group in (TicketGroup.TODO, TicketGroup.WIP)
    ]
    # An incident is still ongoing if it was acknowledged but never resolved
    # (the same acked-minus-resolved logic that drives the stale-ack handling).
    acked = {a.id for a in data.alerts if a.state is AlertState.ACKNOWLEDGED}
    resolved = {a.id for a in data.alerts if a.state is AlertState.RESOLVED}

    def pick(key: str, local: int) -> int:
        return live[key] if key in live else local

    return OpenSummary(
        highest=pick("highest", sum(1 for t in open_tickets if t.is_highest)),
        ps5=pick("ps5", sum(1 for t in open_tickets if t.has_ps5_blockers)),
        ps5_highest=pick("ps5_highest", sum(
            1 for t in open_tickets if t.has_ps5_blockers and t.is_highest)),
        pr_mp=pick("pr_mp", sum(1 for t in open_tickets if t.is_pr_mp_review)),
        # Escalated counts every fetched ISReq ticket in the Jira "Escalated"
        # status, not just active-sprint work: escalation is a cross-sprint state,
        # so this matches the JQL link rather than the sprint-scoped open counts.
        escalated=pick(
            "escalated", sum(1 for t in data.tickets if t.is_isreq and t.is_escalated)),
        ongoing_alerts=pick("ongoing_alerts", len(acked - resolved)),
        highest_url=config.jira_filter_url(config.JIRA_OPEN_FILTERS["highest"]),
        ps5_url=config.jira_filter_url(config.JIRA_OPEN_FILTERS["ps5"]),
        ps5_highest_url=config.jira_filter_url(
            config.JIRA_OPEN_FILTERS["ps5_highest"]
        ),
        pr_mp_url=config.jira_filter_url(config.JIRA_OPEN_FILTERS["pr_mp"]),
        escalated_url=config.jira_jql_url(config.JIRA_ESCALATED_ISREQ_JQL),
        alerts_url=config.pagerduty_open_incidents_url(),
    )


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
    mtta_label: str
    incidents: list[dict]
    # Total ack→resolve time invested across the weekend's incidents (#recap-hours).
    total_time_label: str = "—"
    # In-hours vs off-hours split by the alert's fire time in the on-call's local
    # business-hours window (config.BUSINESS_HOURS_LOCAL): alert counts + invested time.
    in_hours_count: int = 0
    off_hours_count: int = 0
    in_hours_time_label: str = "0m"
    off_hours_time_label: str = "0m"


def build_weekend_recap(db: Database, data: DashboardData, now: datetime) -> WeekendRecap | None:
    """What the previous weekend's on-call engineer dealt with (#145).

    Summarises the PagerDuty incidents the on-call engineer handled over their
    weekend: count, resolved vs still-open, mean trigger→ack (MTTA) and ack→resolve
    (MTTR), and each incident (title + link). Returns ``None`` when no on-call is
    known (no iCal data).

    Loads the weekend's alerts directly from the DB rather than ``data.alerts``,
    because the just-passed weekend can fall in the *previous* pulse (so it isn't
    in the current-pulse merge). An incident counts if the on-call touched it
    within the weekend window; its resolve time is taken whenever it resolved, so
    a resolution that slips just past midnight still reads as resolved.
    """
    if not data.weekend_oncall:
        return None
    # The recap is about the just-passed weekend → the earliest stored entry
    # (the latest entry is the upcoming weekend shown in the header).
    oc = min(data.weekend_oncall, key=lambda w: w.weekend_start)
    name = _display_name(oc.engineer_email)
    tz = _handler_zone(oc.engineer_email) or UTC
    # Half-open weekend window [Sat 00:00, Mon 00:00) in the on-call's timezone.
    start = datetime(oc.weekend_start.year, oc.weekend_start.month, oc.weekend_start.day, tzinfo=tz)
    mon = oc.weekend_end + timedelta(days=1)
    end = datetime(mon.year, mon.month, mon.day, tzinfo=tz)

    in_weekend: set[str] = set()
    trig_at: dict[str, datetime] = {}
    ack_at: dict[str, datetime] = {}
    res_at: dict[str, datetime] = {}
    meta: dict[str, dict] = {}
    for a in accumulated_alerts_since(db, start.astimezone(UTC)):
        # Triggers are handler-less in PagerDuty, so capture their fire time
        # (earliest wins) before the on-call filter — needed for MTTA (trigger→ack).
        if a.state is AlertState.TRIGGERED:
            if a.id not in trig_at or a.at < trig_at[a.id]:
                trig_at[a.id] = a.at
            continue
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

    # Business-hours split keys off the alert's fire time (when it *happened*) in the
    # on-call's own timezone — getting paged at 3am is the burden we want to surface.
    bh_start, bh_end = config.BUSINESS_HOURS_LOCAL

    def _off_hours(iid: str) -> bool:
        fired = trig_at.get(iid) or ack_at.get(iid)
        if fired is None:
            return False
        return not (bh_start <= fired.astimezone(tz).hour < bh_end)

    incidents: list[dict] = []
    mttr_total = mttr_n = 0
    mtta_total = mtta_n = 0
    total_time = 0                          # Σ ack→resolve across incidents
    in_count = off_count = 0               # alerts by fire-time bucket
    in_time = off_time = 0                 # invested time by fire-time bucket
    for iid in in_weekend:
        m = meta[iid]
        resolved = iid in res_at
        duration = None
        if resolved and iid in ack_at and res_at[iid] >= ack_at[iid]:
            duration = (res_at[iid] - ack_at[iid]).total_seconds()
            mttr_total += int(duration)
            mttr_n += 1
        if iid in ack_at and iid in trig_at and ack_at[iid] >= trig_at[iid]:
            mtta_total += int((ack_at[iid] - trig_at[iid]).total_seconds())
            mtta_n += 1
        secs = int(duration) if duration else 0
        total_time += secs
        off = _off_hours(iid)
        if off:
            off_count += 1
            off_time += secs
        else:
            in_count += 1
            in_time += secs
        incidents.append({
            "number": m["number"],
            "title": m["title"] or "(untitled incident)",
            "url": m["url"],
            "resolved": resolved,
            "duration_label": format_duration(duration),
            "off_hours": off,
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
        mtta_label=format_duration(mtta_total / mtta_n) if mtta_n else "—",
        incidents=incidents,
        total_time_label=format_duration(total_time) if total_time else "—",
        in_hours_count=in_count,
        off_hours_count=off_count,
        in_hours_time_label=format_duration(in_time),
        off_hours_time_label=format_duration(off_time),
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
    # Managers/global management get a simple view of their own work: To Do / WIP
    # / Done, no Distractors and no role-based reclassification (#72 follow-up).
    # They hold no coverage slot, so they're always GEN rather than OFF — matching
    # their chip — and never get the day-off reclassification below.
    is_management = eng.is_manager or eng.is_global
    role = Role.GEN if is_management else resolve_roles(
        db, [email], region.timezone, now,
        _pto_today([email], data, region.timezone, now))[email]
    # On a day off, the week's assignment wins over the particular day (#off-distractor):
    # the engineer's own assigned WIP (ISDB + ISReq) and any alert they covered on a
    # day they were *working* count as real work, not distractions. ``weekly`` lets us
    # recover the role they held on each alert's coverage day.
    is_off = role is Role.OFF
    weekly = db.get_weekly_schedule() if is_off else {}

    # ISDB completions count as Success only if done this pulse, so pass the
    # anchored pulse window (region-local) to the classifier (#172).
    today = now.astimezone(ZoneInfo(region.timezone)).date()
    _, pstart, pend = current_pulse(today)
    grouped = classify_for_engineer(
        email, data.tickets, data.touches, data.active_sprint_ids, (pstart, pend)
    )

    # Reclassify assigned tickets into Distractors (To Do / queued work is never a
    # distraction, even when untriaged) — role rules (#86): BVG non-priority, PVG
    # In-Review (yellow), Project non-ISDB (red). Project distractions also pull
    # completed work out of Success, since off-task ISReq is never a success for a
    # Project engineer.
    # Highest-focus toggle (#focus-toggle): *flag* (don't move) in-progress ISReq
    # that isn't Highest, a ps5-blocker, or [PR/MP Review], so it's obvious at a
    # glance when someone is working on the wrong ticket. Captured from WIP *before*
    # the role reclassification below, so the flag still shows on the off-focus
    # ticket wherever its role lands it (most regular ISReq are role-distractors).
    focus_flag_ids: set[str] = set()
    if highest_focus and not is_management:
        focus_flag_ids = {
            t.id for t in grouped[TicketGroup.WIP]
            if t.is_isreq and not (t.is_highest or t.is_pr_mp_review or t.has_ps5_blockers)
        }

    role_distractor_ids: set[str] = set()
    scan_groups = () if is_management else (TicketGroup.WIP, TicketGroup.SUCCESS)
    for grp in scan_groups:
        kept = []
        for t in grouped[grp]:
            # An OFF day must not turn the engineer's own assigned work into
            # distractors — the week's assignment wins over the particular day off
            # (#off-distractor). Work they touched but aren't assigned to is still a
            # distractor (classify_for_engineer already put it in Distractors).
            if is_role_distractor(role, t) and role is not Role.OFF:
                grouped[TicketGroup.DISTRACTORS].append(t)
                role_distractor_ids.add(t.id)
            else:
                kept.append(t)
        grouped[grp] = kept

    touched_24h_ids = {
        tc.ticket_id for tc in data.touches
        if tc.engineer_email == email and tc.at >= now - _24H
    }
    # Worklog this engineer logged per ticket this pulse (#line-time): the same
    # assignee-proxy worklog seconds the panel totals use, broken out per ticket.
    pstart = _pulse_start(now)
    worklog_secs: dict[str, int] = {}
    for tc in data.touches:
        if (tc.engineer_email == email and tc.kind is TouchKind.WORKLOG
                and tc.at >= pstart):
            worklog_secs[tc.ticket_id] = worklog_secs.get(tc.ticket_id, 0) + tc.seconds
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
            is_rd = t.id in role_distractor_ids
            assigned = group is not TicketGroup.DISTRACTORS or is_rd
            if is_off and group in (TicketGroup.WIP, TicketGroup.SUCCESS):
                # Their own assigned work is on-task, not a distraction on a day off
                # — the week's assignment wins over the OFF colouring (#off-distractor).
                color = Color.GREEN
            else:
                color = ticket_color(
                    role, t, assigned=assigned, group=group, role_distractor=is_rd
                )
            secs = worklog_secs.get(t.id, 0)
            effort_label, effort_title, effort_over = _effort_badge(t)
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
                    ribbon=t.priority_ribbon,
                    priority=t.priority or "",
                    flagged=t.id in focus_flag_ids,
                    time_label=hours_label(secs) if secs else "",
                    time_title="Time you logged on this ticket this pulse" if secs else "",
                    effort_label=effort_label,
                    effort_title=effort_title,
                    effort_over=effort_over,
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
    # Fire/ack/resolve times per incident (all handlers) drive each row's duration.
    trig_at, ack_at, res_at = _alert_spans_by_incident(data.alerts)
    meta = db.incident_meta(alert_by_incident.keys())
    distractor_alert_ids: set[str] = set()   # incidents classified as distractions (#distract-share)
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
            # On a day off, classify an alert by the role held on the day it was
            # covered (#off-distractor): on-call work done on a working day stays
            # real work, not a distraction just because today is OFF. Alerts truly
            # covered on an off day resolve to OFF and remain distractions.
            alert_role = (
                _coverage_role(email, region.timezone, weekly, data.weekend_oncall, a.at)
                if is_off else role
            )
            color, target = alert_classification(alert_role, resolved=resolved, recent=recent)
        if target is TicketGroup.DISTRACTORS:
            distractor_alert_ids.add(a.id)
        # Line: "STATUS — #code — Title" (code = PagerDuty incident number).
        parts = ["RES" if resolved else "ACK"]
        if number is not None:
            parts.append(f"#{number}")
        parts.append(title or "alert")
        time_label, time_title = _alert_line_time(a.id, now, trig_at, ack_at, res_at)
        vm = TicketVM(
            key="⚠",
            title=" — ".join(parts),
            color=color,
            url=url,
            touched_24h=recent,
            time_label=time_label,
            time_title=time_title,
        )
        out[target.value].append(vm)

    pulse_start = _pulse_start(now)
    cutoff = now - _24H
    # "Today" = the engineer's local calendar day so far (midnight → now), in the
    # region's timezone — distinct from the rolling-24h cutoff above (#cal).
    today_start = datetime(today.year, today.month, today.day, tzinfo=ZoneInfo(region.timezone))
    # Distractor time per window, shown as a share of open (non-busy) time
    # (#distract-share): worklog on the SRE's distractor tickets + wall-clock time on
    # alerts classified as distractions. Not for management (no distractor view).
    distractor_ids = {t.id for t in grouped[TicketGroup.DISTRACTORS]}

    def _distractor_time(since: datetime) -> int:
        return (_worklog_on_since(email, data, since, distractor_ids)
                + _alert_union_time_since(email, data, since, distractor_alert_ids))

    # Their tickets in the current active sprint per project, linking to that person's
    # sprint board by Jira accountId (#sprint-link) — accountId filters correctly for
    # everyone, including the private-email engineers. Falls back to an email JQL only
    # until the accountId has been fetched once.
    account_id = db.get_account_ids().get(email)

    def _sprint_count(project_key: str) -> int:
        return sum(1 for t in data.tickets
                   if t.assignee_email == email and t.project_key == project_key
                   and t.sprint_id in data.active_sprint_ids)

    def _sprint_url(project_key: str) -> str:
        if account_id:
            return config.jira_sprint_board_url(project_key, account_id)
        return config.jira_jql_url(
            f'project = {project_key.upper()} AND assignee = "{email}"'
            f' AND sprint in openSprints() ORDER BY status DESC')

    return DetailPanelVM(
        email=email, name=eng.name, role=role, groups=out,
        show_distractors=not is_management,
        sprint_isreq_count=_sprint_count(config.PROJECT_ISREQ),
        sprint_isdb_count=_sprint_count(config.PROJECT_ISDB),
        sprint_isreq_url=_sprint_url(config.PROJECT_ISREQ),
        sprint_isdb_url=_sprint_url(config.PROJECT_ISDB),
        distractor_seconds=_distractor_time(pulse_start),
        distractor_24h_seconds=_distractor_time(cutoff),
        distractor_today_seconds=_distractor_time(today_start),
        alert_time_seconds=_alert_time_since(email, data, pulse_start),
        alert_union_seconds=_alert_union_time_since(email, data, pulse_start),
        ticket_time_seconds=_ticket_time_since(email, data, pulse_start),
        jira_project_seconds=_ticket_time_since(email, data, pulse_start, config.PROJECT_ISDB),
        jira_request_seconds=_ticket_time_since(email, data, pulse_start, config.PROJECT_ISREQ),
        alert_time_24h_seconds=_alert_time_since(email, data, cutoff),
        alert_union_24h_seconds=_alert_union_time_since(email, data, cutoff),
        jira_project_24h_seconds=_ticket_time_since(email, data, cutoff, config.PROJECT_ISDB),
        jira_request_24h_seconds=_ticket_time_since(email, data, cutoff, config.PROJECT_ISREQ),
        alert_time_today_seconds=_alert_time_since(email, data, today_start),
        alert_union_today_seconds=_alert_union_time_since(email, data, today_start),
        jira_project_today_seconds=_ticket_time_since(
            email, data, today_start, config.PROJECT_ISDB),
        jira_request_today_seconds=_ticket_time_since(
            email, data, today_start, config.PROJECT_ISREQ),
        pr_stats=data.github_prs.get(email) or GitHubPRStats(),
        pr_stats_24h=data.github_prs_24h.get(email) or GitHubPRStats(),
        pr_stats_today=data.github_prs_today.get(email) or GitHubPRStats(),
        calendar=data.calendar.get(email) or CalendarAvail(),
    )
