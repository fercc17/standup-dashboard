"""Role schedule service (FR-007/008, #71) — T031.

Set weekly default roles, free-text day notes, and a today-only override that
expires at the engineer's region-local midnight. Also parses/applies a
tab-separated paste of the manager's spreadsheet. All persistence is
history-preserving (latest row wins on read).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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


def set_day_note(db: Database, email: str, note_date: str, note: str, now: datetime) -> None:
    """Set/clear a free-text note on a specific date (``YYYY-MM-DD``, #day-notes)."""
    if email not in config.ENGINEERS_BY_EMAIL:
        raise ValueError(f"unknown engineer: {email}")
    try:
        date.fromisoformat(note_date)
    except ValueError as exc:
        raise ValueError(f"invalid note date: {note_date!r}") from exc
    db.set_day_note(email, note_date, note, now)


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
    note_date: str = ""  # ISO date the note applies to (per-date notes, #day-notes)


_MONTHS = {m.lower(): i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _row_date(label: str, weekday: str, ref: date) -> str:
    """ISO date for a paste row label like 'Wed, Jun 10'.

    The year is inferred from ``ref`` (rolled to next year if the month/day is far
    in its past). Falls back to ``weekday``'s date in ``ref``'s week if the label
    carries no parseable month/day, so a note always lands on a concrete date."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})", label)
    if m and (mon := _MONTHS.get(m.group(1).lower())):
        try:
            d = date(ref.year, mon, int(m.group(2)))
            if (ref - d).days > 180:
                d = date(ref.year + 1, mon, int(m.group(2)))
            return d.isoformat()
        except ValueError:
            pass
    monday = ref - timedelta(days=ref.weekday())
    return (monday + timedelta(days=WEEKDAY_SLOTS.index(weekday))).isoformat()


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


def parse_schedule_paste(
    text: str, now: datetime | None = None
) -> tuple[list[PasteAction], list[str]]:
    """Parse a tab-separated schedule paste into actions + human-readable errors.

    Two layouts are supported (engineers as columns, days as rows):

    1. **Day/Role layout** — the manager's spreadsheet, where each engineer spans
       two columns (a blank ``Day`` and a ``Role``)::

           Date<TAB>Afif<TAB><TAB>Alejdg<TAB><TAB>Alex L...
           <TAB>Day<TAB>Role<TAB>Day<TAB>Role...
           Wed, Jun 10<TAB><TAB>PVG<TAB><TAB>GEN...

       Detected by a ``Role`` sub-header or by non-adjacent name columns; each
       engineer's role is read from the column to the right of their name, so a
       trailing empty role never shifts the others.

    2. **Simple layout** — names directly above role cells, with an optional
       leading status column that is right-aligned away::

           Date<TAB>Afif<TAB>Alejdg<TAB>Alex L<TAB>Colin<TAB>Matt<TAB>Nick
           Wed, Jun 10<TAB>OK<TAB>PVG<TAB>GEN<TAB>PS7+<TAB>BVG<TAB>OFF

    Names match on email, full/first name or alias. Day rows start with a weekday
    label (Mon..Fri; weekend/unrecognised rows are ignored). PVG/GEN/BVG/OFF map
    directly; any other non-blank value is Project (raw text kept as a day note).
    """
    ref = (now or datetime.now(UTC)).date()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return [], ["paste needs a header row of engineer names plus at least one day row"]

    lookup = _roster_lookup()
    header = lines[0].split("\t")

    # Positional emails over header[1:] (None for a blank or unknown column).
    engineers: list[str | None] = []
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

    # Paired Day/Role layout: a 'Role' sub-header, or names sitting at every-other
    # column (a blank Day column between them). Then the role is the cell to the
    # right of each name — robust to trailing empty cells trimmed on copy.
    positions = [i for i, e in enumerate(engineers) if e is not None]
    has_role_subheader = any(
        c.strip().lower() == "role" for ln in lines[1:] for c in ln.split("\t")
    )
    non_adjacent = len(positions) >= 2 and all(
        b - a >= 2 for a, b in zip(positions, positions[1:])
    )
    paired = has_role_subheader or non_adjacent

    n = len(engineers)
    actions: list[PasteAction] = []
    for line in lines[1:]:
        cells = line.split("\t")
        weekday = _weekday_of(cells[0]) if cells else None
        if weekday is None:
            continue  # weekend or unrecognized day row → skipped
        if paired:
            # engineers[i] sits at header column i+1; its role is the next column.
            pairs = []
            for i, email in enumerate(engineers):
                if email is None:
                    continue
                col = i + 2
                pairs.append((email, cells[col] if col < len(cells) else ""))
        else:
            # Right-align role cells so a leading status column (e.g. 'OK') drops.
            values = cells[1:]
            if len(values) > n:
                values = values[-n:]
            pairs = [
                (engineers[i], values[i])
                for i in range(min(n, len(values)))
                if engineers[i] is not None
            ]
        note_date = _row_date(cells[0], weekday, ref)
        for email, cell in pairs:
            role, note = _classify_cell(cell)
            if role is not None or note is not None:
                actions.append(PasteAction(email=email, weekday=weekday, role=role,
                                           note=note, note_date=note_date))
    return actions, errors


def apply_schedule_paste(db: Database, text: str, now: datetime) -> dict:
    """Parse and persist a schedule paste; return a small summary for the UI."""
    actions, errors = parse_schedule_paste(text, now)
    roles = notes = 0
    for a in actions:
        if a.role is not None:
            set_weekly_role(db, a.email, a.weekday, a.role, now)
            roles += 1
        if a.note is not None:
            set_day_note(db, a.email, a.note_date, a.note, now)
            notes += 1
    return {"roles": roles, "notes": notes, "errors": errors}
