"""Role schedule service (FR-007/008/010) — T031.

Set weekly default roles, set a today-only override that expires at the
engineer's region-local midnight, and read/write the BVG strict-mode flag in
``ui_state``. All persistence is history-preserving (latest row wins on read).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.models import WEEKDAYS, Role
from ..storage.db import Database

STRICT_KEY = "bvg_strict_mode"


def set_weekly_role(db: Database, email: str, weekday: str, role: str, now: datetime) -> None:
    if email not in config.ENGINEERS_BY_EMAIL:
        raise ValueError(f"unknown engineer: {email}")
    if weekday not in WEEKDAYS:
        raise ValueError(f"unknown weekday: {weekday}")
    Role(role)  # validate
    db.set_weekly_role(email, weekday, role, now)


def _next_region_midnight(timezone: str, now_utc: datetime) -> tuple[datetime, date]:
    """Return (next region-local midnight as UTC, region-local today's date)."""
    zone = ZoneInfo(timezone)
    local = now_utc.astimezone(zone)
    today = local.date()
    next_midnight_local = datetime.combine(today + timedelta(days=1), datetime.min.time(), zone)
    return next_midnight_local.astimezone(now_utc.tzinfo), today


def set_today_override(db: Database, email: str, role: str, now: datetime) -> None:
    """Set a today-only override expiring at the engineer's region midnight."""
    eng = config.ENGINEERS_BY_EMAIL.get(email)
    if eng is None:
        raise ValueError(f"unknown engineer: {email}")
    Role(role)  # validate
    region_key = config.primary_region_for(email) or config.REGION_KEYS[0]
    tz = config.REGIONS[region_key].timezone
    expires_at, effective_date = _next_region_midnight(tz, now)
    db.set_override(email, role, effective_date, expires_at, now)


def get_strict_mode(db: Database) -> bool:
    return db.get_ui_state(STRICT_KEY, "off") == "on"


def set_strict_mode(db: Database, on: bool, now: datetime) -> None:
    db.set_ui_state(STRICT_KEY, "on" if on else "off", now)
