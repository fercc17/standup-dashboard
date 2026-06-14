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
class DetailPanelVM:
    email: str
    name: str
    role: Role
    groups: dict[str, list[TicketVM]] = field(default_factory=dict)


# Per-pulse summary metrics persisted for the growing pulse-history table (#80).
PULSE_SUMMARY_FIELDS = (
    "new_highest", "new_pr_mp", "new_ps5", "new_regular", "new_total",
    "closed_highest", "closed_pr_mp", "closed_ps5", "closed_total", "isdb_closed",
    "alerts_ack", "alerts_resolved", "alerts_total",
    "alert_mttr_sum", "alert_mttr_n",   # sum-of-seconds / count → mean time to resolve
    "alert_mtta_sum", "alert_mtta_n",   # sum-of-seconds / count → mean time to acknowledge
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
    # green/yellow/red bands for the five alert cells (None = neutral, no colour).
    ack_level: Color | None = None
    resolved_level: Color | None = None
    total_level: Color | None = None
    mttr_level: Color | None = None
    mtta_level: Color | None = None
    # Closed PR/MP vs New PR/MP Review keep-up band (#141), None = no activity.
    closed_pr_mp_level: Color | None = None

    @property
    def mttr_label(self) -> str:
        return format_duration(self.alert_mttr_seconds)

    @property
    def mtta_label(self) -> str:
        return format_duration(self.alert_mtta_seconds)


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
    # green/yellow/red bands for the five alert cells (None = neutral, no colour).
    ack_level: Color | None = None
    resolved_level: Color | None = None
    total_level: Color | None = None
    mttr_level: Color | None = None
    mtta_level: Color | None = None
    # Closed PR/MP vs New PR/MP Review keep-up band (#141), None = no activity.
    closed_pr_mp_level: Color | None = None

    @property
    def mttr_label(self) -> str:
        return format_duration(self.alert_mttr_seconds)

    @property
    def mtta_label(self) -> str:
        return format_duration(self.alert_mtta_seconds)
