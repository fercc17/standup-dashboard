"""Pure role × ticket → color matrix and role-based grouping rules (#86).

No I/O, no globals — exhaustively unit-tested in ``tests/unit/test_coloring.py``.
These rules supersede the original spec matrix and the BVG strict-mode toggle.

Assigned, non-Done tickets (after role-based reclassification, see
``is_role_distractor``):

| Role    | kept-assigned color                              | role distraction |
|---------|--------------------------------------------------|------------------|
| PVG     | red (tickets are a distraction from alerts)      | —                |
| BVG     | green (only Highest / [PR/MP Review] are kept)    | red              |
| GEN     | green iff ISReq Highest/ps5, else red            | —                |
| Project | green (only ISDB is kept)                        | red              |
| OFF     | red                                              | —                |

Non-assigned touches: PVG/BVG green; GEN/Project/OFF red.
FR-017: a Done (Success) ticket is green — except for a Project engineer, whose
non-ISDB work is an off-task RED distraction even when completed.
PVG alerts (resolved → green Success, ack → yellow WIP) are surfaced in the
panel builder, which is the general case for all roles.
"""

from __future__ import annotations

from .models import Color, Role, Ticket, TicketGroup

_NON_ASSIGNED: dict[Role, Color] = {
    Role.PVG: Color.GREEN,
    Role.BVG: Color.GREEN,
    Role.GEN: Color.RED,
    Role.PROJECT: Color.RED,
    Role.OFF: Color.RED,
}

TICKET_KINDS = ("highest", "pr_mp", "ps5", "regular", "isdb")

# Final role × ticket-kind colour matrix (#158, supersedes #86). A cell that is
# not GREEN is an off-task *distraction* for that role.
_TICKET_MATRIX: dict[Role, dict[str, Color]] = {
    Role.PVG:     {"highest": Color.YELLOW, "pr_mp": Color.YELLOW, "ps5": Color.YELLOW, "regular": Color.RED,   "isdb": Color.YELLOW},
    Role.BVG:     {"highest": Color.GREEN,  "pr_mp": Color.GREEN,  "ps5": Color.GREEN,  "regular": Color.RED,   "isdb": Color.RED},
    Role.GEN:     {"highest": Color.GREEN,  "pr_mp": Color.YELLOW, "ps5": Color.GREEN,  "regular": Color.RED,   "isdb": Color.RED},
    Role.PROJECT: {"highest": Color.RED,    "pr_mp": Color.RED,    "ps5": Color.RED,    "regular": Color.RED,   "isdb": Color.GREEN},
    Role.OFF:     {"highest": Color.RED,    "pr_mp": Color.RED,    "ps5": Color.RED,    "regular": Color.RED,   "isdb": Color.RED},
}


def ticket_kind(ticket: Ticket) -> str:
    """Which matrix column a ticket falls in. ISDB is its own column; ISReq goes
    by precedence Highest → [PR/MP Review] → ps5-blocker → regular."""
    if ticket.is_isdb:
        return "isdb"
    if ticket.is_highest:
        return "highest"
    if ticket.is_pr_mp_review:
        return "pr_mp"
    if ticket.has_ps5_blockers:
        return "ps5"
    return "regular"


def _matrix_color(role: Role, ticket: Ticket) -> Color:
    return _TICKET_MATRIX.get(role, {}).get(ticket_kind(ticket), Color.RED)


def is_role_distractor(role: Role, ticket: Ticket) -> bool:
    """Whether an assigned ticket is an off-task distraction for ``role`` (#158).

    A ticket distracts when its matrix colour is not GREEN. Done (Success) work is
    never a distraction — except a Project engineer's non-ISDB work, which stays
    off-task even when completed (FR-017 exception)."""
    if ticket.group is TicketGroup.SUCCESS:
        return role is Role.PROJECT and not ticket.is_isdb
    return _matrix_color(role, ticket) is not Color.GREEN


def ticket_color(
    role: Role,
    ticket: Ticket,
    *,
    assigned: bool,
    group: TicketGroup | None = None,
    role_distractor: bool = False,  # accepted for call-site compatibility; unused
) -> Color:
    """Resolve a ticket's colour for an engineer with ``role`` (#158 matrix).

    Done (Success) is green for everyone except a Project engineer's non-ISDB
    work. Non-assigned touches use the per-role default; assigned tickets read
    straight from the matrix (which already encodes the distractor yellow/red)."""
    eff_group = group or ticket.group
    if eff_group is TicketGroup.SUCCESS:
        if role is Role.PROJECT and not ticket.is_isdb:
            return Color.RED
        return Color.GREEN
    if not assigned:
        return _NON_ASSIGNED[role]
    return _matrix_color(role, ticket)


def alert_classification(role: Role, *, resolved: bool, recent: bool) -> tuple[Color, TicketGroup]:
    """(colour, group) for an alert handled by ``role`` (#158). ``recent`` = ≤24h.

    PVG own alert duty, so their alerts are real work: green resolved (Success),
    yellow open ≤24h / red open >24h (WIP). BVG yellow either way. GEN / Project /
    OFF: alerts are an off-task distraction (red, Distractors)."""
    if role is Role.PVG:
        if resolved:
            return Color.GREEN, TicketGroup.SUCCESS
        return (Color.YELLOW if recent else Color.RED), TicketGroup.WIP
    if role is Role.BVG:
        return Color.YELLOW, (TicketGroup.SUCCESS if resolved else TicketGroup.WIP)
    return Color.RED, TicketGroup.DISTRACTORS


def alert_color(role: Role) -> Color:
    """Representative alert colour for ``role`` (a yellow open alert), for non-
    stateful uses such as a legend swatch."""
    color, _ = alert_classification(role, resolved=False, recent=True)
    return color


# --- Alert counts-table cell coloring (green / yellow / red bands) ----------
# Volume columns (Ack, Total) are judged against an on-call "fatigue" cap that
# the caller scales by the row's span (weekday / weekend / pulse) AND the number
# of selected regions — more engineers on call ⇒ a higher healthy ceiling. The
# resolve rate and the MTTR/MTTA means are *rates*, not volumes, so they share
# fixed thresholds and are NOT scaled by region count.
ALERT_RES_GREEN = 0.80            # resolved ≥80% of acked → keeping pace (green)
ALERT_RES_YELLOW = 0.50           # 50–79% slipping (yellow); <50% backlog (red)
ALERT_MTTA_GREEN_S = 5 * 60       # ≤5m ack latency is healthy
ALERT_MTTA_YELLOW_S = 15 * 60     # 5–15m slipping; >15m alerts go unnoticed (red)
ALERT_MTTR_GREEN_S = 30 * 60      # ≤30m is a tidy resolve
ALERT_MTTR_YELLOW_S = 2 * 60 * 60  # 30m–2h acceptable; >2h painful (red)


def count_level(count: int, green_cap: int) -> Color | None:
    """Volume band: green ≤ cap, yellow ≤ 2×cap, red beyond (None if no cap)."""
    if green_cap <= 0:
        return None
    if count <= green_cap:
        return Color.GREEN
    if count <= 2 * green_cap:
        return Color.YELLOW
    return Color.RED


def ack_vs_triggered_level(triggered: int, ack: int, regions: int) -> Color | None:
    """Ack-vs-Triggered keep-up (#169): acks should track what fired. Green when
    the shortfall (triggered − ack) is within 1 per region, yellow within 2 per
    region, red beyond — e.g. for one region Trig 5 / Ack 3 is yellow, Ack ≤ 2 is
    red. Neutral when nothing fired (or it isn't attributable)."""
    if triggered <= 0:
        return None
    margin = max(regions, 1)
    gap = triggered - ack
    if gap <= margin:
        return Color.GREEN
    if gap <= 2 * margin:
        return Color.YELLOW
    return Color.RED


def resolve_rate_level(resolved: int, acked: int) -> Color | None:
    """Resolved-vs-acked band; None when there was nothing to acknowledge."""
    if acked <= 0:
        return None
    rate = resolved / acked
    if rate >= ALERT_RES_GREEN:
        return Color.GREEN
    if rate >= ALERT_RES_YELLOW:
        return Color.YELLOW
    return Color.RED


def _duration_level(seconds: float | None, green_max: float, yellow_max: float) -> Color | None:
    """Lower-is-better band: green ≤ green_max, yellow ≤ yellow_max, else red."""
    if seconds is None:
        return None
    if seconds <= green_max:
        return Color.GREEN
    if seconds <= yellow_max:
        return Color.YELLOW
    return Color.RED


def mttr_level(seconds: float | None) -> Color | None:
    """Ack→resolve mean band (#140): ≤30m green, 30m–2h yellow, >2h red."""
    return _duration_level(seconds, ALERT_MTTR_GREEN_S, ALERT_MTTR_YELLOW_S)


def mtta_level(seconds: float | None) -> Color | None:
    """Trigger→ack mean band (#140): ≤5m green, 5–15m yellow, >15m red."""
    return _duration_level(seconds, ALERT_MTTA_GREEN_S, ALERT_MTTA_YELLOW_S)


ALERT_MTTA_TREND_TOLERANCE = 0.10  # within ±10% of the previous pulse counts as "same"
ALERT_MTTR_TREND_TOLERANCE = 0.10  # within ±10% of the previous pulse counts as "same"


def _floored_trend_level(
    current_s: float | None, previous_s: float | None, floor_s: float, tolerance: float
) -> Color | None:
    """Lower-is-better trend band with a green floor (#149 follow-up): green at or
    below ``floor_s``; above it, colour vs the *previous pulse* — green when
    meaningfully faster (lower), red when slower, yellow when about the same
    (±``tolerance``). Neutral if there's no current data, or above the floor with
    no previous-pulse baseline."""
    if current_s is None:
        return None
    if current_s <= floor_s:
        return Color.GREEN
    if previous_s is None or previous_s <= 0:
        return None
    if current_s < previous_s * (1 - tolerance):
        return Color.GREEN
    if current_s > previous_s * (1 + tolerance):
        return Color.RED
    return Color.YELLOW


def mtta_trend_level(
    current_s: float | None, previous_s: float | None
) -> Color | None:
    """Pulse-history MTTA colour: always green at or below the healthy 5m floor
    (ALERT_MTTA_GREEN_S); above it, green/red/yellow vs the previous pulse."""
    return _floored_trend_level(
        current_s, previous_s, ALERT_MTTA_GREEN_S, ALERT_MTTA_TREND_TOLERANCE)


def mttr_trend_level(
    current_s: float | None, previous_s: float | None
) -> Color | None:
    """Pulse-history MTTR colour: always green at or below the healthy 30m floor
    (ALERT_MTTR_GREEN_S); above it, green/red/yellow vs the previous pulse."""
    return _floored_trend_level(
        current_s, previous_s, ALERT_MTTR_GREEN_S, ALERT_MTTR_TREND_TOLERANCE)


ALERT_WIP_GREEN_DAYS = 2     # ≤2 days in progress is healthy
ALERT_WIP_YELLOW_DAYS = 5    # 3–5 days is ageing; >5 days is stale (red)


def wip_age_level(age_seconds: float | None) -> Color | None:
    """Aging-WIP band (#147): ≤2d green, 3–5d yellow, >5d red; None if not WIP."""
    if age_seconds is None:
        return None
    days = age_seconds / 86400
    if days <= ALERT_WIP_GREEN_DAYS:
        return Color.GREEN
    if days <= ALERT_WIP_YELLOW_DAYS:
        return Color.YELLOW
    return Color.RED


def closed_vs_new_level(closed: int, new: int) -> Color | None:
    """Closed vs New for Highest / ps5 (#155): more closed than new is green,
    equal yellow (including 0 = 0), fewer red."""
    if closed > new:
        return Color.GREEN
    if closed == new:
        return Color.YELLOW
    return Color.RED


def closed_vs_new_total_level(closed: int, new: int, regions: int) -> Color | None:
    """Closed vs New Total with a ±2-per-region margin (#155): green when closed
    leads new by ≥2×regions, red when it trails by ≥2×regions, yellow in between."""
    if closed == 0 and new == 0:
        return None
    margin = 2 * max(regions, 1)
    diff = closed - new
    if diff >= margin:
        return Color.GREEN
    if diff <= -margin:
        return Color.RED
    return Color.YELLOW


INTAKE_TREND_MIN_MARGIN = 2  # ignore a ±1-ticket wobble between pulses


def intake_level(
    current: int, previous: int | None, floor: float | None = None
) -> Color | None:
    """New-column (intake) colour (#147). When a healthy ``floor`` is given (the
    historical average New Total), a positive intake at or below it is green —
    normal load. Otherwise colour by the change vs the previous pulse: fewer new
    tickets than last pulse is green (less incoming work), more is red, about the
    same yellow. The 'same' band is ±max(2, 10% of the previous count) so small-
    count noise stays yellow. Neutral with no previous pulse, or when both this
    and the previous pulse are zero (a sustained quiet/no-data stretch)."""
    if floor is not None and 0 < current <= floor:
        return Color.GREEN
    if previous is None or (current == 0 and previous == 0):
        return None
    margin = max(INTAKE_TREND_MIN_MARGIN, round(0.10 * previous))
    diff = current - previous
    if diff <= -margin:
        return Color.GREEN
    if diff >= margin:
        return Color.RED
    return Color.YELLOW


CYCLE_TREND_TOLERANCE = 0.10   # within ±10% of the previous pulse counts as "same"


def cycle_trend_level(
    current_days: float | None, previous_days: float | None
) -> Color | None:
    """Days-to-close trend vs the previous pulse (#147): green when meaningfully
    faster (lower), red when slower, yellow when about the same (±10%). Neutral
    without a previous-pulse baseline."""
    if current_days is None or previous_days is None or previous_days <= 0:
        return None
    if current_days < previous_days * (1 - CYCLE_TREND_TOLERANCE):
        return Color.GREEN
    if current_days > previous_days * (1 + CYCLE_TREND_TOLERANCE):
        return Color.RED
    return Color.YELLOW


def cycle_color(
    current_days: float | None, previous_days: float | None, closed: int, new: int
) -> Color | None:
    """Days-to-close cell colour (#147): green when this pulse out-closed intake
    (closed > new) — clearing a backlog of *old* tickets inflates cycle time, so a
    rise there is a win, not a regression — otherwise colour by trend vs the
    previous pulse (see ``cycle_trend_level``). Neutral when there's no cycle data."""
    if current_days is None:
        return None
    if closed > new:
        return Color.GREEN
    return cycle_trend_level(current_days, previous_days)


def pr_mp_review_level(review_new: int, closed: int) -> Color | None:
    """Closed PR/MP vs reviews requested (#141): are we keeping up with reviews?

    ``review_new`` is the New PR/MP Review count, ``closed`` the Closed PR/MP
    count. Green when we closed at least as many as came in (closing *more* is
    fine — another region may have left one), yellow when exactly one is left
    behind, red when two or more are. Neutral when there was no PR/MP activity.
    """
    if review_new == 0 and closed == 0:
        return None
    deficit = review_new - closed
    if deficit <= 0:
        return Color.GREEN
    if deficit == 1:
        return Color.YELLOW
    return Color.RED
