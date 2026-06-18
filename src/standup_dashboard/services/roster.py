"""Editable roster: add SREs + move them between regions (#16).

Layered over the static seed in ``config``: DB-backed additions and region
moves are merged into the live roster via ``config.rebuild_roster()`` so the
whole app (chips, counts, schedule, identity) sees the change.
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import config
from ..storage.db import Database

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load(db: Database) -> None:
    """Rebuild the live config roster from the DB overrides."""
    additions = tuple(
        config.EngineerConfig(
            name=name, email=email, region_keys=(region,), github_login=github_login
        )
        for name, email, region, github_login in db.get_roster_additions()
    )
    config.rebuild_roster(additions, db.get_region_overrides())


def add_engineer(
    db: Database, name: str, email: str, region: str, now: datetime, github_login: str = ""
) -> None:
    name = (name or "").strip()
    email = (email or "").strip().lower()
    # GitHub logins are case-insensitive; store lowercased, "@handle" → "handle".
    github_login = (github_login or "").strip().lstrip("@").lower()
    if not name:
        raise ValueError("name is required")
    if not _EMAIL_RE.match(email):
        raise ValueError(f"invalid email: {email!r}")
    if region not in config.REGION_KEYS:
        raise ValueError(f"unknown region: {region}")
    db.add_roster_engineer(name, email, region, now, github_login)
    load(db)


def move_engineer(db: Database, email: str, region: str, now: datetime) -> None:
    email = (email or "").strip().lower()
    if email not in config.ENGINEERS_BY_EMAIL:
        raise ValueError(f"unknown engineer: {email}")
    if region not in config.REGION_KEYS:
        raise ValueError(f"unknown region: {region}")
    db.set_region_override(email, region, now)
    load(db)
