"""Domain dataclasses + presentation view models (data-model.md §1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Role(StrEnum):
    PVG = "PVG"
    BVG = "BVG"
    GEN = "GEN"
    PROJECT = "Project"
    OFF = "OFF"


class Color(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class TicketGroup(StrEnum):
    TODO = "To Do"
    WIP = "WIP"
    SUCCESS = "Success"
    DISTRACTORS = "Distractors"


# Status name → group (FR-013). Real Jira workflows use many custom status
# names, so grouping primarily keys off Jira's statusCategory (below) and uses
# these explicit names only as a fallback when no category is present.
STATUS_GROUP: dict[str, TicketGroup] = {
    "To Do": TicketGroup.TODO,
    "Untriaged": TicketGroup.TODO,
    "Triaged": TicketGroup.TODO,
    "Blocked": TicketGroup.TODO,
    "In Progress": TicketGroup.WIP,
    "In Review": TicketGroup.WIP,
    "Done": TicketGroup.SUCCESS,
}

# Jira statusCategory name → group (robust across custom status names).
STATUS_CATEGORY_GROUP: dict[str, TicketGroup] = {
    "To Do": TicketGroup.TODO,
    "In Progress": TicketGroup.WIP,
    "Done": TicketGroup.SUCCESS,
}

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "WEEKEND")
# Editable role slots in the schedule modal — the weekend has no role (#71); its
# coverage is "who's around" (the on-call), resolved from the iCal feed.
WEEKDAY_SLOTS = ("MON", "TUE", "WED", "THU", "FRI")
ISREQ_REVIEW_PREFIX = "[PR/MP Review]"
# The real Jira label is singular "ps5-blocker"; accept the plural too, defensively.
PS5_BLOCKER_LABELS = ("ps5-blocker", "ps5-blockers")
PRIORITY_HIGHEST = "Highest"


class TouchKind(StrEnum):
    STATUS = "status"
    COMMENT = "comment"
    ASSIGNMENT = "assignment"
    WORKLOG = "worklog"
    LINK = "link"


class AlertState(StrEnum):
    TRIGGERED = "triggered"        # incident fired — handler-less; used for MTTA only
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# Static / config-derived
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    key: str
    timezone: str
    manager_email: str
    member_emails: tuple[str, ...]


@dataclass(frozen=True)
class Engineer:
    email: str
    name: str
    region_keys: tuple[str, ...]
    is_manager: bool = False
    is_global: bool = False


# ---------------------------------------------------------------------------
# Fetched / derived
# ---------------------------------------------------------------------------


@dataclass
class Pulse:
    project_key: str
    sprint_id: int
    name: str
    start: datetime
    end: datetime
    state: str = "active"


@dataclass
class Ticket:
    id: str
    project_key: str
    title: str
    status: str
    priority: str | None
    labels: list[str] = field(default_factory=list)
    assignee_email: str | None = None
    sprint_id: int | None = None
    is_done_date: date | None = None
    created: datetime | None = None
    status_category: str | None = None
    reporter_email: str | None = None
    wip_since: datetime | None = None   # start of the current In-Progress streak (#147)

    def wip_age_seconds(self, now: datetime) -> float | None:
        """How long the ticket has sat in its current WIP streak, or None."""
        if self.wip_since is None:
            return None
        return (now - self.wip_since).total_seconds()

    @property
    def group(self) -> TicketGroup | None:
        # Prefer Jira's statusCategory (covers custom status names); fall back
        # to explicit status-name mapping when no category is available.
        if self.status_category and self.status_category in STATUS_CATEGORY_GROUP:
            return STATUS_CATEGORY_GROUP[self.status_category]
        return STATUS_GROUP.get(self.status)

    @property
    def is_isreq(self) -> bool:
        return self.project_key == "ISReq"

    @property
    def is_isdb(self) -> bool:
        return self.project_key == "ISDB"

    @property
    def is_highest(self) -> bool:
        return self.priority == PRIORITY_HIGHEST

    @property
    def has_ps5_blockers(self) -> bool:
        return any(label.lower() in PS5_BLOCKER_LABELS for label in self.labels)

    @property
    def is_bvg_review(self) -> bool:
        """ISReq ticket whose title starts with ``[PR/MP Review]`` (FR-015)."""
        return self.is_isreq and self.title.strip().startswith(ISREQ_REVIEW_PREFIX)

    @property
    def is_pr_mp_review(self) -> bool:
        """Any ticket whose title starts with ``[PR/MP Review]`` (project-agnostic)."""
        return self.title.strip().startswith(ISREQ_REVIEW_PREFIX)


@dataclass
class TouchEvent:
    ticket_id: str
    engineer_email: str
    kind: TouchKind
    at: datetime
    seconds: int = 0  # logged work time (TouchKind.WORKLOG only); 0 otherwise (#167)


@dataclass
class Alert:
    id: str
    handler_email: str
    state: AlertState
    at: datetime
    title: str | None = None     # PagerDuty incident title ("what went down")
    url: str | None = None       # PagerDuty incident link
    number: int | None = None    # PagerDuty incident number (the alert code)


@dataclass
class WeekendOnCall:
    engineer_email: str
    weekend_start: date
    weekend_end: date


@dataclass
class FetchSnapshot:
    id: int
    fetched_at: datetime
    jira_ok: bool
    pagerduty_ok: bool
    ical_ok: bool
    raw_path: str


@dataclass
class RoleOverride:
    engineer_email: str
    role: Role
    effective_date: date
    expires_at: datetime


# ---------------------------------------------------------------------------
# Presentation view models (not persisted)
# ---------------------------------------------------------------------------


@dataclass
class ChipVM:
    email: str
    name: str
    role: Role
    is_manager: bool
    touched_24h: int
    alerts_ack_24h: int
    alerts_resolved_24h: int
    region_key: str
    # Two-row metrics: last 24h and since the start of the pulse (#chip-metrics).
    assigned_open: int = 0          # current open assigned work (To Do + WIP), in pulse
    completed_24h: int = 0
    touched_pulse: int = 0
    completed_pulse: int = 0
    alerts_ack_pulse: int = 0
    alerts_resolved_pulse: int = 0


@dataclass
class TicketVM:
    key: str
    title: str
    color: Color
    is_bvg_review: bool = False
    url: str | None = None  # Jira browse / PagerDuty link
    touched_24h: bool = False  # touched in the last 24h (for the panel split, #17)
    pulses_open: int = 0  # Highest + open: full pulses it has stayed open, 0 if fresh (#18)
    status: str = ""  # Jira status name, shown on the ticket line


@dataclass
class GitHubPRStats:
    """Per-engineer GitHub PR activity within the current pulse window (#173).

    Four Search-API counts, scoped to the configured org:
      * ``created``  — PRs the engineer opened this pulse;
      * ``merged``   — PRs they authored that merged this pulse;
      * ``updated``  — PRs they authored with any activity this pulse;
      * ``reviewed`` — PRs they reviewed that were active this pulse. GitHub has
        no review-date qualifier, so this is approximated by ``reviewed-by`` over
        the pulse's ``updated`` window (a review bumps the PR's updated time).
    """
    created: int = 0
    merged: int = 0
    updated: int = 0
    reviewed: int = 0


@dataclass
class CalendarAvail:
    """Per-engineer calendar occupancy this pulse, from the free/busy iCal feed
    (#cal). Classified by duration only (the public feed has no titles): >8h or
    all-day = PTO, ~4h = SD time (one/week, day marked), >1h = blocker, ≤1h =
    meeting.

    ``busy`` = the meetings only (≤1h blocks, merged); blockers and SD are not
    counted as busy. ``open`` = capacity (40h/week) − busy; >1h blockers (off-time
    between shifts) and PTO do not reduce it.
    """
    busy_seconds: int = 0
    open_seconds: int = 0
    pto_seconds: int = 0
    sd_days: tuple[str, ...] = ()  # weekday abbrevs carrying the 4h SD block
    has_data: bool = False         # False when the calendar isn't reachable/public


@dataclass
class DetailPanelVM:
    email: str
    name: str
    role: Role
    groups: dict[str, list[TicketVM]] = field(default_factory=dict)
    # Time spent this pulse so far (#167, #173). Alerts come two ways:
    #   * ``alert_time_seconds`` — ack→resolve summed per incident this SRE
    #     resolved (overlapping incidents counted in both, the original metric);
    #   * ``alert_union_seconds`` — the same intervals merged into wall-clock
    #     time, so concurrent incidents aren't double-counted (≤ the overlap one).
    # Jira worklog time, split by project: ISDB (``jira_project_seconds``) vs
    # ISReq (``jira_request_seconds``); ``ticket_time_seconds`` is their total.
    alert_time_seconds: int = 0
    alert_union_seconds: int = 0
    ticket_time_seconds: int = 0
    jira_project_seconds: int = 0
    jira_request_seconds: int = 0
    # Per-pulse GitHub PR activity for this SRE (#173); zeros when GitHub isn't
    # configured or the engineer isn't mapped to a login.
    pr_stats: GitHubPRStats = field(default_factory=GitHubPRStats)
    # Calendar occupancy this pulse (#cal); has_data False when not public/reachable.
    calendar: CalendarAvail = field(default_factory=CalendarAvail)

    @property
    def alert_time_label(self) -> str:
        return hours_label(self.alert_time_seconds)

    @property
    def alert_union_label(self) -> str:
        return hours_label(self.alert_union_seconds)

    @property
    def ticket_time_label(self) -> str:
        return hours_label(self.ticket_time_seconds)

    @property
    def jira_project_label(self) -> str:
        return hours_label(self.jira_project_seconds)

    @property
    def jira_request_label(self) -> str:
        return hours_label(self.jira_request_seconds)

    @property
    def total_time_seconds(self) -> int:
        """Headline hands-on time this pulse: alert wall-clock (no-overlap) + Jira
        worklog on ISDB + ISReq. Shown next to the role (#173)."""
        return self.alert_union_seconds + self.jira_project_seconds + self.jira_request_seconds

    @property
    def total_time_label(self) -> str:
        return hours_label(self.total_time_seconds)

    @property
    def cal_busy_label(self) -> str:
        return hours_label(self.calendar.busy_seconds)

    @property
    def cal_open_label(self) -> str:
        return hours_label(self.calendar.open_seconds)


# Per-pulse summary metrics persisted for the growing pulse-history table (#80).
PULSE_SUMMARY_FIELDS = (
    "new_highest", "new_pr_mp", "new_ps5", "new_regular", "new_total",
    "closed_highest", "closed_pr_mp", "closed_ps5", "closed_total", "isdb_closed",
    "alerts_triggered", "alerts_ack", "alerts_resolved", "alerts_total",
    "alert_mttr_sum", "alert_mttr_n",   # sum-of-seconds / count → mean time to resolve
    "alert_mtta_sum", "alert_mtta_n",   # sum-of-seconds / count → mean time to acknowledge
    "ticket_cycle_sum", "ticket_cycle_n",  # sum-of-days / count → mean ISReq cycle time (#147)
)


def format_duration(seconds: float | None) -> str:
    """Human-friendly duration ('45m', '2h 15m', '1d 3h'); em dash when None."""
    if seconds is None:
        return "—"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def hours_label(seconds: float | None) -> str:
    """Work-time label like '6h 30m' / '45m' / '0m' — hours never roll into days
    (a per-pulse effort total reads better as '32h' than '1d 8h')."""
    if not seconds:
        return "0m"
    minutes = int(round(seconds / 60))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def delta_label(delta_s: float | None) -> str:
    """Signed duration change vs a previous bucket, e.g. '▲2m' (up, worse for
    MTTR/MTTA) / '▼3m' (down). Blank when there's no baseline or the change
    rounds to under a minute. Shared by the pulse-history and counts tables."""
    if delta_s is None or round(abs(delta_s) / 60) == 0:
        return ""
    return f"{'▲' if delta_s > 0 else '▼'}{format_duration(abs(delta_s))}"


@dataclass
class PulseHistoryRow:
    pulse_number: int
    label: str
    # metric name → Cell(count, breakdown) for the per-person hover tooltip (#80).
    cells: dict[str, Cell] = field(default_factory=dict)
    region_pct: float | None = None        # selected regions' share of all alerts that pulse
    closed_pct: float | None = None        # selected regions' share of all ISReq closed
    isdb_closed_pct: float | None = None   # selected regions' share of all ISDB closed
    alert_mttr_seconds: float | None = None  # mean ack→resolve time this pulse, None if no data
    alert_mtta_seconds: float | None = None  # mean trigger→ack time this pulse, None if no data
    mttr_delta_seconds: float | None = None  # MTTR change vs the previous pulse, None = no baseline (#149)
    mtta_delta_seconds: float | None = None  # MTTA change vs the previous pulse, None = no baseline (#149)
    ticket_cycle_days: float | None = None   # mean ISReq created→done days this pulse (#147)
    # green/yellow/red bands for the alert cells (None = neutral, no colour).
    triggered_level: Color | None = None
    ack_level: Color | None = None
    resolved_level: Color | None = None
    total_level: Color | None = None
    mttr_level: Color | None = None
    mtta_level: Color | None = None
    # Days-to-close trend band vs the previous pulse (#147), None = no baseline.
    cycle_level: Color | None = None
    # Closed PR/MP vs New PR/MP Review keep-up band (#141), None = no activity.
    closed_pr_mp_level: Color | None = None
    # Closed-vs-New bands for Highest / ps5 / Total (#155), None = no activity.
    closed_highest_level: Color | None = None
    closed_ps5_level: Color | None = None
    closed_total_level: Color | None = None
    # Intake (New columns) trend vs the previous pulse (#147): fewer new = green.
    new_highest_level: Color | None = None
    new_pr_mp_level: Color | None = None
    new_ps5_level: Color | None = None
    new_regular_level: Color | None = None
    new_total_level: Color | None = None

    @property
    def mttr_label(self) -> str:
        return format_duration(self.alert_mttr_seconds)

    @property
    def mtta_label(self) -> str:
        return format_duration(self.alert_mtta_seconds)

    _delta_label = staticmethod(delta_label)  # back-compat alias

    @property
    def mttr_delta_label(self) -> str:
        return delta_label(self.mttr_delta_seconds)

    @property
    def mtta_delta_label(self) -> str:
        return delta_label(self.mtta_delta_seconds)

    @property
    def cycle_label(self) -> str:
        """Mean ISReq cycle time, e.g. '3.4d' or '—' (#147)."""
        if self.ticket_cycle_days is None:
            return "—"
        return f"{self.ticket_cycle_days:.1f}d"


@dataclass
class Cell:
    """One counts-table number plus a per-person breakdown for its tooltip (#91).

    ``breakdown`` maps a person's display name → how many of the counted items
    are attributed to them (reporter for new tickets, assignee for closed
    tickets, handler for alerts).
    """
    count: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def tip(self) -> str:
        """Tooltip text: ``Name ×N`` lines, most-active first."""
        if not self.breakdown:
            return ""
        items = sorted(self.breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
        return "\n".join(f"{name} ×{n}" for name, n in items)


@dataclass
class CountsRow:
    """One row of the pulse counts table — a region-local day or the pulse total.

    Ticket cells are ISDB-scoped (#91): the four ``new_*`` buckets are mutually
    exclusive and sum to ``new_total``; ``closed_*`` are ISDB completions that
    day (``closed_highest``/``closed_ps5`` are subcounts of ``closed_total``).
    Alert cells are scoped to the selected regions' members.
    """
    label: str
    is_weekend: bool = False
    is_total: bool = False
    new_highest: Cell = field(default_factory=Cell)
    new_pr_mp: Cell = field(default_factory=Cell)
    new_ps5: Cell = field(default_factory=Cell)
    new_regular: Cell = field(default_factory=Cell)
    new_total: Cell = field(default_factory=Cell)
    closed_highest: Cell = field(default_factory=Cell)
    # Closed [PR/MP Review] tickets, attributed by ASSIGNEE (owner) region — a
    # deliberate exception to the creation-time region used by the other columns.
    closed_pr_mp: Cell = field(default_factory=Cell)
    closed_ps5: Cell = field(default_factory=Cell)
    closed_total: Cell = field(default_factory=Cell)
    isdb_closed: Cell = field(default_factory=Cell)
    alerts_triggered: Cell = field(default_factory=Cell)
    alerts_ack: Cell = field(default_factory=Cell)
    alerts_resolved: Cell = field(default_factory=Cell)
    alerts_total: Cell = field(default_factory=Cell)
    region_alert_pct: float | None = None
    closed_pct: float | None = None  # region's share of all ISReq closed tickets
    isdb_closed_pct: float | None = None  # region's share of all ISDB closed tickets
    is_previous: bool = False  # the previous-pulse comparison row (#80)
    alert_mttr_seconds: float | None = None  # mean ack→resolve time this row, None if no data
    alert_mtta_seconds: float | None = None  # mean trigger→ack time this row, None if no data
    alert_mttr_n: int = 0  # incidents behind the MTTR mean (for the tooltip)
    alert_mtta_n: int = 0  # incidents behind the MTTA mean (for the tooltip)
    # MTTR/MTTA change vs the previous bucket: a day row vs the previous day, the
    # Pulse total vs the previous pulse. None = no baseline / no data.
    mttr_delta_seconds: float | None = None
    mtta_delta_seconds: float | None = None
    # green/yellow/red bands for the alert cells (None = neutral, no colour).
    triggered_level: Color | None = None
    ack_level: Color | None = None
    resolved_level: Color | None = None
    total_level: Color | None = None
    mttr_level: Color | None = None
    mtta_level: Color | None = None
    # Closed PR/MP vs New PR/MP Review keep-up band (#141), None = no activity.
    closed_pr_mp_level: Color | None = None
    # Closed-vs-New bands for Highest / ps5 / Total (#155), None = no activity.
    closed_highest_level: Color | None = None
    closed_ps5_level: Color | None = None
    closed_total_level: Color | None = None

    @property
    def mttr_label(self) -> str:
        return format_duration(self.alert_mttr_seconds)

    @property
    def mtta_label(self) -> str:
        return format_duration(self.alert_mtta_seconds)

    @property
    def mttr_delta_label(self) -> str:
        return delta_label(self.mttr_delta_seconds)

    @property
    def mtta_delta_label(self) -> str:
        return delta_label(self.mtta_delta_seconds)
