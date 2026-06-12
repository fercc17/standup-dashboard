"""Effective-role resolution (FR-009) — pure logic over the region timezone.

Resolution order for an engineer on a given region-local day:
  1. active (non-expired) today-only override, else
  2. weekly default for that region-local weekday, else
  3. the WEEKEND rule on Sat/Sun (default OFF), else a weekday default.

The region timezone defines "today" and the weekday. Override expiry is
handled by the storage layer (``Database.get_active_overrides`` filters on
``expires_at``); this module receives only the currently-active overrides.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Role

# Default when no weekly schedule row exists yet for a weekday slot.
DEFAULT_WEEKDAY_ROLE = Role.GEN
DEFAULT_WEEKEND_ROLE = Role.OFF

_WEEKDAY_SLOTS = ("MON", "TUE", "WED", "THU", "FRI")


def region_weekday(now_utc: datetime, timezone: str) -> str:
    """Return the schedule slot (MON..FRI or WEEKEND) for the region-local day."""
    local = now_utc.astimezone(ZoneInfo(timezone))
    wd = local.weekday()  # 0=Mon .. 6=Sun
    if wd >= 5:
        return "WEEKEND"
    return _WEEKDAY_SLOTS[wd]


def is_weekend(now_utc: datetime, timezone: str) -> bool:
    return region_weekday(now_utc, timezone) == "WEEKEND"


def effective_role(
    email: str,
    timezone: str,
    now_utc: datetime,
    weekly_schedule: dict[tuple[str, str], str],
    active_overrides: dict[str, str],
) -> Role:
    """Resolve an engineer's effective role for the region-local "today"."""
    # 1. Today-only override.
    if email in active_overrides:
        return Role(active_overrides[email])

    slot = region_weekday(now_utc, timezone)

    # 2. Weekly default for that slot.
    role_str = weekly_schedule.get((email, slot))
    if role_str is not None:
        return Role(role_str)

    # 3. Slot default.
    return DEFAULT_WEEKEND_ROLE if slot == "WEEKEND" else DEFAULT_WEEKDAY_ROLE
