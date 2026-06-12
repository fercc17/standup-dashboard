"""SQLite schema + history-preserving access (data-model.md §2, FR-028).

Fetched-data rows are append-only, keyed by ``fetch_id``; they are never
updated or deleted, so each fetch is a full historical layer. State/config
tables (role schedule, overrides, ui_state) keep history via row versioning —
the latest row wins on read, older rows are retained.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from ..domain.models import (
    Alert,
    AlertState,
    FetchSnapshot,
    Pulse,
    Ticket,
    TouchEvent,
    TouchKind,
    WeekendOnCall,
)

DEFAULT_DB_PATH = Path("data/dashboard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_snapshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    jira_ok       INTEGER NOT NULL,
    pagerduty_ok  INTEGER NOT NULL,
    ical_ok       INTEGER NOT NULL,
    raw_path      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket (
    fetch_id       INTEGER NOT NULL REFERENCES fetch_snapshot(id),
    id             TEXT NOT NULL,
    project_key    TEXT NOT NULL,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL,
    priority       TEXT,
    labels_json    TEXT NOT NULL,
    assignee_email TEXT,
    sprint_id      INTEGER,
    is_done_date   TEXT,
    created        TEXT,
    status_category TEXT,
    reporter_email TEXT,
    PRIMARY KEY (fetch_id, id)
);

CREATE TABLE IF NOT EXISTS touch_event (
    fetch_id       INTEGER NOT NULL REFERENCES fetch_snapshot(id),
    ticket_id      TEXT NOT NULL,
    engineer_email TEXT NOT NULL,
    kind           TEXT NOT NULL,
    at             TEXT NOT NULL,
    PRIMARY KEY (fetch_id, ticket_id, engineer_email, kind, at)
);

CREATE TABLE IF NOT EXISTS alert (
    fetch_id       INTEGER NOT NULL REFERENCES fetch_snapshot(id),
    id             TEXT NOT NULL,
    handler_email  TEXT NOT NULL,
    state          TEXT NOT NULL,
    at             TEXT NOT NULL,
    title          TEXT,
    url            TEXT,
    PRIMARY KEY (fetch_id, id, handler_email, state)
);

CREATE TABLE IF NOT EXISTS pulse (
    fetch_id     INTEGER NOT NULL REFERENCES fetch_snapshot(id),
    project_key  TEXT NOT NULL,
    sprint_id    INTEGER NOT NULL,
    name         TEXT NOT NULL,
    start        TEXT NOT NULL,
    end          TEXT NOT NULL,
    state        TEXT NOT NULL,
    PRIMARY KEY (fetch_id, project_key)
);

CREATE TABLE IF NOT EXISTS weekend_oncall (
    fetch_id       INTEGER NOT NULL REFERENCES fetch_snapshot(id),
    engineer_email TEXT NOT NULL,
    weekend_start  TEXT NOT NULL,
    weekend_end    TEXT NOT NULL,
    PRIMARY KEY (fetch_id, engineer_email, weekend_start)
);

CREATE TABLE IF NOT EXISTS role_schedule (
    engineer_email TEXT NOT NULL,
    weekday        TEXT NOT NULL,
    role           TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_override (
    engineer_email TEXT NOT NULL,
    role           TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_note (
    engineer_email TEXT NOT NULL,
    weekday        TEXT NOT NULL,
    note           TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_state (
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roster_addition (
    email      TEXT NOT NULL,
    name       TEXT NOT NULL,
    region     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS region_override (
    email      TEXT NOT NULL,
    region     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_role_schedule_latest
    ON role_schedule (engineer_email, weekday, updated_at);
CREATE INDEX IF NOT EXISTS idx_day_note_latest
    ON day_note (engineer_email, weekday, updated_at);
CREATE INDEX IF NOT EXISTS idx_ui_state_latest
    ON ui_state (key, updated_at);
"""


class Database:
    """Thin SQLite wrapper. History-preserving by construction."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Idempotent column additions for databases created by older schemas."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(ticket)")}
        for name, decl in (
            ("created", "TEXT"),
            ("status_category", "TEXT"),
            ("reporter_email", "TEXT"),
        ):
            if name not in cols:
                self._conn.execute(f"ALTER TABLE ticket ADD COLUMN {name} {decl}")
        alert_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(alert)")}
        for name, decl in (("title", "TEXT"), ("url", "TEXT")):
            if name not in alert_cols:
                self._conn.execute(f"ALTER TABLE alert ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self._conn.close()

    # -- fetch snapshots -----------------------------------------------------

    def create_fetch_snapshot(
        self,
        fetched_at: datetime,
        jira_ok: bool,
        pagerduty_ok: bool,
        ical_ok: bool,
        raw_path: str,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO fetch_snapshot (fetched_at, jira_ok, pagerduty_ok, ical_ok, raw_path)"
            " VALUES (?, ?, ?, ?, ?)",
            (fetched_at.isoformat(), int(jira_ok), int(pagerduty_ok), int(ical_ok), raw_path),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def latest_fetch(self) -> FetchSnapshot | None:
        row = self._conn.execute(
            "SELECT * FROM fetch_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_snapshot(row) if row else None

    def latest_good_fetch(self) -> FetchSnapshot | None:
        """Most recent snapshot where Jira succeeded (for last-good fallback)."""
        row = self._conn.execute(
            "SELECT * FROM fetch_snapshot WHERE jira_ok = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_snapshot(row) if row else None

    def count_fetch_snapshots(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM fetch_snapshot").fetchone()[0])

    def fetches_since(self, since: datetime) -> list[FetchSnapshot]:
        """All snapshots fetched at/after ``since``, oldest first (for merging, #88)."""
        rows = self._conn.execute(
            "SELECT * FROM fetch_snapshot WHERE fetched_at >= ? ORDER BY id ASC",
            (since.isoformat(),),
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    # -- append-only writes of fetched data ----------------------------------

    def insert_tickets(self, fetch_id: int, tickets: Iterable[Ticket]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO ticket"
            " (fetch_id, id, project_key, title, status, priority, labels_json,"
            "  assignee_email, sprint_id, is_done_date, created, status_category,"
            "  reporter_email)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    fetch_id, t.id, t.project_key, t.title, t.status, t.priority,
                    json.dumps(t.labels), t.assignee_email, t.sprint_id,
                    t.is_done_date.isoformat() if t.is_done_date else None,
                    t.created.isoformat() if t.created else None,
                    t.status_category, t.reporter_email,
                )
                for t in tickets
            ],
        )
        self._conn.commit()

    def insert_touches(self, fetch_id: int, touches: Iterable[TouchEvent]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO touch_event (fetch_id, ticket_id, engineer_email, kind, at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(fetch_id, t.ticket_id, t.engineer_email, t.kind.value, t.at.isoformat())
             for t in touches],
        )
        self._conn.commit()

    def insert_alerts(self, fetch_id: int, alerts: Iterable[Alert]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO alert"
            " (fetch_id, id, handler_email, state, at, title, url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(fetch_id, a.id, a.handler_email, a.state.value, a.at.isoformat(), a.title, a.url)
             for a in alerts],
        )
        self._conn.commit()

    def insert_pulses(self, fetch_id: int, pulses: Iterable[Pulse]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO pulse"
            " (fetch_id, project_key, sprint_id, name, start, end, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(fetch_id, p.project_key, p.sprint_id, p.name,
              p.start.isoformat(), p.end.isoformat(), p.state) for p in pulses],
        )
        self._conn.commit()

    def insert_weekend_oncall(self, fetch_id: int, entries: Iterable[WeekendOnCall]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO weekend_oncall"
            " (fetch_id, engineer_email, weekend_start, weekend_end) VALUES (?, ?, ?, ?)",
            [(fetch_id, w.engineer_email, w.weekend_start.isoformat(), w.weekend_end.isoformat())
             for w in entries],
        )
        self._conn.commit()

    # -- reads of a fetch layer ----------------------------------------------

    def get_tickets(self, fetch_id: int) -> list[Ticket]:
        rows = self._conn.execute(
            "SELECT * FROM ticket WHERE fetch_id = ?", (fetch_id,)
        ).fetchall()
        return [_row_to_ticket(r) for r in rows]

    def get_touches(self, fetch_id: int) -> list[TouchEvent]:
        rows = self._conn.execute(
            "SELECT * FROM touch_event WHERE fetch_id = ?", (fetch_id,)
        ).fetchall()
        return [
            TouchEvent(r["ticket_id"], r["engineer_email"], TouchKind(r["kind"]),
                       datetime.fromisoformat(r["at"]))
            for r in rows
        ]

    def get_alerts(self, fetch_id: int) -> list[Alert]:
        rows = self._conn.execute(
            "SELECT * FROM alert WHERE fetch_id = ?", (fetch_id,)
        ).fetchall()
        return [
            Alert(r["id"], r["handler_email"], AlertState(r["state"]),
                  datetime.fromisoformat(r["at"]), title=r["title"], url=r["url"])
            for r in rows
        ]

    def get_pulses(self, fetch_id: int) -> list[Pulse]:
        rows = self._conn.execute(
            "SELECT * FROM pulse WHERE fetch_id = ?", (fetch_id,)
        ).fetchall()
        return [
            Pulse(r["project_key"], r["sprint_id"], r["name"],
                  datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]), r["state"])
            for r in rows
        ]

    def get_weekend_oncall(self, fetch_id: int) -> list[WeekendOnCall]:
        rows = self._conn.execute(
            "SELECT * FROM weekend_oncall WHERE fetch_id = ?", (fetch_id,)
        ).fetchall()
        return [
            WeekendOnCall(r["engineer_email"], date.fromisoformat(r["weekend_start"]),
                          date.fromisoformat(r["weekend_end"]))
            for r in rows
        ]

    # -- role schedule (latest row wins per engineer+weekday) ----------------

    def set_weekly_role(self, engineer_email: str, weekday: str, role: str,
                        now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO role_schedule (engineer_email, weekday, role, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (engineer_email, weekday, role, now.isoformat()),
        )
        self._conn.commit()

    def get_weekly_schedule(self) -> dict[tuple[str, str], str]:
        """Latest role per (engineer_email, weekday)."""
        rows = self._conn.execute(
            "SELECT engineer_email, weekday, role FROM role_schedule rs"
            " WHERE updated_at = ("
            "   SELECT MAX(updated_at) FROM role_schedule"
            "   WHERE engineer_email = rs.engineer_email AND weekday = rs.weekday)"
        ).fetchall()
        return {(r["engineer_email"], r["weekday"]): r["role"] for r in rows}

    # -- day notes (latest row wins per engineer+weekday) --------------------

    def set_day_note(self, engineer_email: str, weekday: str, note: str,
                     now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO day_note (engineer_email, weekday, note, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (engineer_email, weekday, note, now.isoformat()),
        )
        self._conn.commit()

    def get_day_notes(self) -> dict[tuple[str, str], str]:
        """Latest free-text day note per (engineer_email, weekday)."""
        rows = self._conn.execute(
            "SELECT engineer_email, weekday, note FROM day_note dn"
            " WHERE updated_at = ("
            "   SELECT MAX(updated_at) FROM day_note"
            "   WHERE engineer_email = dn.engineer_email AND weekday = dn.weekday)"
        ).fetchall()
        return {(r["engineer_email"], r["weekday"]): r["note"] for r in rows}

    # -- role override -------------------------------------------------------

    def set_override(self, engineer_email: str, role: str, effective_date: date,
                     expires_at: datetime, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO role_override"
            " (engineer_email, role, effective_date, expires_at, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (engineer_email, role, effective_date.isoformat(), expires_at.isoformat(),
             now.isoformat()),
        )
        self._conn.commit()

    def get_active_overrides(self, now: datetime) -> dict[str, str]:
        """Latest non-expired override role per engineer (expires_at > now)."""
        rows = self._conn.execute(
            "SELECT engineer_email, role, expires_at, created_at FROM role_override ro"
            " WHERE expires_at > ?"
            " AND created_at = ("
            "   SELECT MAX(created_at) FROM role_override"
            "   WHERE engineer_email = ro.engineer_email AND expires_at > ?)",
            (now.isoformat(), now.isoformat()),
        ).fetchall()
        return {r["engineer_email"]: r["role"] for r in rows}

    # -- ui_state ------------------------------------------------------------

    def set_ui_state(self, key: str, value: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO ui_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now.isoformat()),
        )
        self._conn.commit()

    def get_ui_state(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM ui_state WHERE key = ? ORDER BY updated_at DESC LIMIT 1",
            (key,),
        ).fetchone()
        return row["value"] if row else default

    # -- roster overrides (added engineers + region moves, #16) --------------

    def add_roster_engineer(self, name: str, email: str, region: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO roster_addition (email, name, region, created_at) VALUES (?, ?, ?, ?)",
            (email, name, region, now.isoformat()),
        )
        self._conn.commit()

    def get_roster_additions(self) -> list[tuple[str, str, str]]:
        """Latest (name, email, region) per added engineer."""
        rows = self._conn.execute(
            "SELECT name, email, region FROM roster_addition ra WHERE created_at = ("
            "  SELECT MAX(created_at) FROM roster_addition WHERE email = ra.email)"
        ).fetchall()
        return [(r["name"], r["email"], r["region"]) for r in rows]

    def set_region_override(self, email: str, region: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO region_override (email, region, updated_at) VALUES (?, ?, ?)",
            (email, region, now.isoformat()),
        )
        self._conn.commit()

    def get_region_overrides(self) -> dict[str, str]:
        """Latest region per engineer that has been moved."""
        rows = self._conn.execute(
            "SELECT email, region FROM region_override ro WHERE updated_at = ("
            "  SELECT MAX(updated_at) FROM region_override WHERE email = ro.email)"
        ).fetchall()
        return {r["email"]: r["region"] for r in rows}


def _row_to_snapshot(row: sqlite3.Row) -> FetchSnapshot:
    return FetchSnapshot(
        id=row["id"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        jira_ok=bool(row["jira_ok"]),
        pagerduty_ok=bool(row["pagerduty_ok"]),
        ical_ok=bool(row["ical_ok"]),
        raw_path=row["raw_path"],
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        project_key=row["project_key"],
        title=row["title"],
        status=row["status"],
        priority=row["priority"],
        labels=json.loads(row["labels_json"]),
        assignee_email=row["assignee_email"],
        sprint_id=row["sprint_id"],
        is_done_date=date.fromisoformat(row["is_done_date"]) if row["is_done_date"] else None,
        created=datetime.fromisoformat(row["created"]) if row["created"] else None,
        status_category=row["status_category"],
        reporter_email=row["reporter_email"],
    )
