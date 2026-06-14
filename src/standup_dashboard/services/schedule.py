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


HIGHEST_FOCUS_KEY = "highest_focus"
SHOW_MANAGEMENT_KEY = "show_management"


def get_highest_focus(db: Database) -> bool:
    """Whether the 'Highest only' focus toggle is on (#86 follow-up)."""
    return db.get_ui_state(HIGHEST_FOCUS_KEY, "off") == "on"


def set_highest_focus(db: Database, on: bool, now: datetime) -> None:
    db.set_ui_state(HIGHEST_FOCUS_KEY, "on" if on else "off", now)


def get_show_management(db: Database) -> bool:
    """Whether the Management chip group is shown (#151). Default on."""
    return db.get_ui_state(SHOW_MANAGEMENT_KEY, "on") == "on"


def set_show_management(db: Database, on: bool, now: datetime) -> None:
    db.set_ui_state(SHOW_MANAGEMENT_KEY, "on" if on else "off", now)


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
    """Header label (lowercased) → email, for schedulable (non-management) engineers.

    Matches on email, full name, first name, and any configured aliases so the
    manager's short spreadsheet headers (e.g. ``Alejdg``, ``Nick``, ``Alex L``)
    resolve to the right engineer.
    """
    lookup: dict[str, str] = {}
    for e in config.ROSTER:
        if e.is_manager or e.is_global:
            continue
        keys = [e.email, e.name, e.name.split()[0], *e.aliases]
        for k in keys:
            lookup[k.lower()] = e.email
    return lookup


def _weekday_of(label: str) -> str | None:
    """Map a row label like 'Thu, Jun 12' or 'thu' to a MON..FRI slot (else None)."""
    token = re.split(r"[,\s]+", label.strip())[0][:3].upper()
    return token if token in _DAY_SLOTS else None


def _classify_cell(cell: str) -> tuple[str | None, str | None]:
    """Map a grid cell to (role, note).

    Blank → (None, None). A token matching a role (PVG/GEN/BVG/OFF/Project,
    case-insensitive) → that role with no note. Anything else (e.g. ``PS7+``) →
    the Project role, keeping the raw token as a day note (#71).
    """
    token = cell.strip()
    if not token:
        return None, None
    role = _ROLE_BY_KEY.get(token.lower())
    if role is not None:
        return role, None
    return Role.PROJECT.value, token


def parse_schedule_paste(text: str) -> tuple[list[PasteAction], list[str]]:
    """Parse a tab-separated schedule paste into actions + human-readable errors.

    Format (matches the transposed modal grid — engineers as columns, days as
    rows):

        Date<TAB>Afif<TAB>Alejdg<TAB>Alex L<TAB>Colin<TAB>Matt<TAB>Nick
        Wed, Jun 10<TAB>OK<TAB>PVG<TAB>GEN<TAB>PS7+<TAB>BVG<TAB>OFF

    The first row names each engineer (display name, first name, alias or email).
    Each later row starts with a day label (Mon..Fri; weekend rows are ignored).
    Role cells are right-aligned to the engineer columns, so a leading status
    column (e.g. ``OK``) is ignored. PVG/GEN/BVG/OFF map directly; any other
    non-blank value is treated as Project (keeping the raw text as a day note).
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return [], ["paste needs a header row of engineer names plus at least one day row"]

    lookup = _roster_lookup()
    header = lines[0].split("\t")
    engineers: list[str | None] = []  # emails, positionally (header[1:] = engineers)
    errors: list[str] = []
    for name in header[1:]:
        nm = name.strip()
        if not nm:
            engineers.append(None)
            continue
        email = lookup.get(nm.lower())
        if email is None:
            errors.append(f"unknown engineer in header: {nm!r}")
        engineers.append(email)

    n = len(engineers)
    actions: list[PasteAction] = []
    for line in lines[1:]:
        cells = line.split("\t")
        weekday = _weekday_of(cells[0]) if cells else None
        if weekday is None:
            continue  # weekend or unrecognized day row → skipped
        values = cells[1:]
        if len(values) > n:
            values = values[-n:]  # drop leading extras (e.g. an 'OK' status column)
        for i, cell in enumerate(values):
            email = engineers[i] if i < n else None
            if email is None:
                continue
            role, note = _classify_cell(cell)
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
