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


# Status name → group (FR-013)
STATUS_GROUP: dict[str, TicketGroup] = {
    "To Do": TicketGroup.TODO,
    "Untriaged": TicketGroup.TODO,
    "Blocked": TicketGroup.TODO,
    "In Progress": TicketGroup.WIP,
    "In Review": TicketGroup.WIP,
    "Done": TicketGroup.SUCCESS,
}

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "WEEKEND")
ISREQ_REVIEW_PREFIX = "[PR/MP Review]"
PS5_BLOCKERS_LABEL = "ps5-blockers"
PRIORITY_HIGHEST = "Highest"


class TouchKind(StrEnum):
    STATUS = "status"
    COMMENT = "comment"
    ASSIGNMENT = "assignment"
    WORKLOG = "worklog"
    LINK = "link"


class AlertState(StrEnum):
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

    @property
    def group(self) -> TicketGroup | None:
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
        return PS5_BLOCKERS_LABEL in self.labels

    @property
    def is_bvg_review(self) -> bool:
        """ISReq ticket whose title starts with ``[PR/MP Review]`` (FR-015)."""
        return self.is_isreq and self.title.strip().startswith(ISREQ_REVIEW_PREFIX)


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


@dataclass
class TicketVM:
    key: str
    title: str
    color: Color
    is_bvg_review: bool = False


@dataclass
class DetailPanelVM:
    email: str
    name: str
    role: Role
    groups: dict[str, list[TicketVM]] = field(default_factory=dict)


@dataclass
class CountsRow:
    label: str
    is_weekend: bool
    open_highest_isreq: int
    new_highest_isreq_24h: int
    isdb_completed: int
    open_ps5_blockers: int
    new_ps5_blockers_24h: int
    alerts_ack: int
    alerts_resolved: int
    alerts_total: int
    region_alert_pct: float | None
    open_pr_mp_review: int = 0
    is_total: bool = False
