"""Role schedule service (FR-007/008, #71) — T031.

Set weekly default roles, free-text day notes, and a today-only override that
expires at the engineer's region-local midnight. Also parses/applies a
tab-separated paste of the manager's spreadsheet. All persistence is
history-preserving (latest row wins on read).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..domain.models import WEEKDAY_SLOTS, WEEKDAYS, Role
from ..storage.db import Database


def set_weekly_role(db: Database, email: str, weekday: str, role: str, now: datetime) -> None:
    if email not in config.ENGINEERS_BY_EMAIL:
        raise ValueError(f"unknown engineer: {email}")
    if weekday not in WEEKDAYS:
        raise ValueError(f"unknown weekday: {weekday}")
    Role(role)  # validate
    db.set_weekly_role(email, weekday, role, now)


def set_day_note(db: Database, email: str, weekday: str, note: str, now: datetime) -> None:
    if email not in config.ENGINEERS_BY_EMAIL:
        raise ValueError(f"unknown engineer: {email}")
    if weekday not in WEEKDAYS:
        raise ValueError(f"unknown weekday: {weekday}")
    db.set_day_note(email, weekday, note, now)


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


# ---------------------------------------------------------------------------
# Spreadsheet paste (#71)
# ---------------------------------------------------------------------------

_ROLE_BY_KEY = {r.value.lower(): r.value for r in Role} | {r.name.lower(): r.value for r in Role}
_DAY_SLOTS = set(WEEKDAY_SLOTS)


@dataclass
class PasteAction:
    email: str
    weekday: str
    role: str | None = None
    note: str | None = None


def _roster_lookup() -> dict[str, str]:
    """Name/email (lowercased) → email, for schedulable (non-management) engineers."""
    lookup: dict[str, str] = {}
    for e in config.ROSTER:
        if e.is_manager or e.is_global:
            continue
        lookup[e.email.lower()] = e.email
        lookup[e.name.lower()] = e.email
    return lookup


def _weekday_of(label: str) -> str | None:
    """Map a row label like 'Thu, Jun 12' or 'thu' to a MON..FRI slot (else None)."""
    token = re.split(r"[,\s]+", label.strip())[0][:3].upper()
    return token if token in _DAY_SLOTS else None


def _parse_cell(cell: str) -> tuple[str | None, str | None, str | None]:
    """Parse a grid cell into (role, note, error). Blank → (None, None, None)."""
    cell = cell.strip()
    if not cell:
        return None, None, None
    role_part, _, note_part = cell.partition("|")
    role_token = role_part.strip()
    note = note_part.strip() or None
    if not role_token:
        return None, note, None
    role = _ROLE_BY_KEY.get(role_token.lower())
    if role is None:
        return None, note, f"unknown role {role_token!r}"
    return role, note, None


def parse_schedule_paste(text: str) -> tuple[list[PasteAction], list[str]]:
    """Parse a tab-separated schedule paste into actions + human-readable errors.

    Format (matches the modal grid, dates as rows, engineers as columns):

        Date<TAB>Alexandre Gomes<TAB>Colin Misare<TAB>...
        Mon, Jun 08<TAB>PVG<TAB>BVG | 1:1 at 3pm<TAB>...
        Tue, Jun 09<TAB>GEN<TAB>OFF<TAB>...

    The first row is a header naming each engineer (by display name or email).
    Each later row starts with a day label (Mon..Fri; weekend rows are ignored)
    and one cell per engineer: ``ROLE``, ``ROLE | note``, or ``| note``.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return [], ["paste needs a header row of engineer names plus at least one day row"]

    lookup = _roster_lookup()
    header = lines[0].split("\t")
    columns: list[str | None] = [None]  # column 0 is the day label
    errors: list[str] = []
    for name in header[1:]:
        email = lookup.get(name.strip().lower())
        if email is None and name.strip():
            errors.append(f"unknown engineer in header: {name.strip()!r}")
        columns.append(email)

    actions: list[PasteAction] = []
    for line in lines[1:]:
        cells = line.split("\t")
        weekday = _weekday_of(cells[0]) if cells else None
        if weekday is None:
            continue  # weekend or unrecognized day row → skipped
        for idx in range(1, len(cells)):
            email = columns[idx] if idx < len(columns) else None
            if email is None:
                continue
            role, note, err = _parse_cell(cells[idx])
            if err:
                errors.append(f"{cells[0].strip()} / column {idx}: {err}")
            if role is not None or note is not None:
                actions.append(PasteAction(email=email, weekday=weekday, role=role, note=note))
    return actions, errors


def apply_schedule_paste(db: Database, text: str, now: datetime) -> dict:
    """Parse and persist a schedule paste; return a small summary for the UI."""
    actions, errors = parse_schedule_paste(text)
    roles = notes = 0
    for a in actions:
        if a.role is not None:
            set_weekly_role(db, a.email, a.weekday, a.role, now)
            roles += 1
        if a.note is not None:
            set_day_note(db, a.email, a.weekday, a.note, now)
            notes += 1
    return {"roles": roles, "notes": notes, "errors": errors}
