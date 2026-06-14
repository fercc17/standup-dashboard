"""Repeat-offender alert analysis (#146): chronic alerts worth fixing.

A repeat offender is an alert *signature* — its title with the volatile
Alertmanager ``[FIRING:n]`` / ``[RESOLVED]`` prefix stripped — that is still
firing now (≥1 incident in the last ``RECENT_DAYS`` days) **and** has fired more
than ``YEAR_MIN`` times this calendar year. The year history lives in the
``incident`` table (bootstrapped from the historical backfill, topped up by every
refresh), so the analysis runs from stored data each day with no extra fetching.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .. import config
from ..domain.models import Alert

RECENT_DAYS = 10   # an alert is "still firing" if seen within this many days
YEAR_MIN = 10      # ... and a repeat offender if it fired MORE than this YTD

# Volatile Alertmanager prefix: "[FIRING:3]" / "[RESOLVED]" — the count changes
# as alerts group/ungroup, so it is not part of the alert's identity.
_PREFIX = re.compile(r"^\s*\[(?:firing|resolved)[^\]]*\]\s*", re.I)


def incident_signature(title: str | None) -> tuple[str, str]:
    """(display, group-key) for an alert title: strip the volatile prefix; the
    key is whitespace/case-normalised so flapping variants collapse together."""
    display = _PREFIX.sub("", title or "").strip()
    return display, " ".join(display.split()).lower()


@dataclass
class IncidentRecord:
    id: str
    signature: str
    fired_at: datetime
    title: str
    number: int | None = None
    url: str | None = None


def incidents_from_alerts(alerts: Iterable[Alert]) -> list[IncidentRecord]:
    """Collapse Alert *events* into one record per distinct incident.

    ``fired_at`` is the earliest event time (the trigger); title/number/url come
    from the first event carrying them. Incidents whose title normalises to an
    empty signature are dropped (nothing to group on)."""
    agg: dict[str, dict] = {}
    for a in alerts:
        display, key = incident_signature(a.title)
        g = agg.get(a.id)
        if g is None:
            g = agg[a.id] = {"fired_at": a.at, "key": "", "title": "",
                             "number": None, "url": None}
        if a.at < g["fired_at"]:
            g["fired_at"] = a.at
        if key and not g["key"]:
            g["key"], g["title"] = key, display
        if a.number is not None and g["number"] is None:
            g["number"], g["url"] = a.number, a.url
    return [
        IncidentRecord(iid, g["key"], g["fired_at"], g["title"], g["number"], g["url"])
        for iid, g in agg.items() if g["key"]
    ]


@dataclass
class OffenderRow:
    title: str
    year_count: int        # incidents this calendar year (the headline number)
    recent_count: int      # incidents in the last RECENT_DAYS days
    number: int | None
    url: str | None
    handlers: list[str]    # who handled it in the last RECENT_DAYS days


def _recent_handlers(db, cutoff: datetime) -> dict[str, list[str]]:
    """signature → sorted handler names seen within the recent window."""
    from .counts import accumulated_alerts_since  # local: avoid an import cycle

    names: dict[str, set[str]] = {}
    for a in accumulated_alerts_since(db, cutoff):
        if a.at < cutoff or not a.handler_email:
            continue
        _, key = incident_signature(a.title)
        if not key:
            continue
        eng = config.ENGINEERS_BY_EMAIL.get(a.handler_email)
        names.setdefault(key, set()).add(eng.name if eng else a.handler_email)
    return {k: sorted(v) for k, v in names.items()}


def build_offenders(
    db, now: datetime, *, recent_days: int = RECENT_DAYS, year_min: int = YEAR_MIN
) -> list[OffenderRow]:
    """Alerts firing in the last ``recent_days`` days that have fired > ``year_min``
    times this calendar year, ranked by yearly frequency (#146)."""
    year0 = datetime(now.year, 1, 1, tzinfo=UTC)
    cutoff = now - timedelta(days=recent_days)
    groups: dict[str, dict] = {}
    for r in db.get_incidents_since(year0):
        fired = datetime.fromisoformat(r["fired_at"])
        g = groups.get(r["signature"])
        if g is None:
            g = groups[r["signature"]] = {"year": 0, "recent": 0, "latest": fired,
                                          "title": r["title"], "number": r["number"],
                                          "url": r["url"]}
        g["year"] += 1
        if fired >= cutoff:
            g["recent"] += 1
        if fired >= g["latest"]:   # representative = the most-recent incident
            g["latest"], g["title"] = fired, r["title"]
            g["number"], g["url"] = r["number"], r["url"]

    handlers = _recent_handlers(db, cutoff)
    rows = [
        OffenderRow(title=g["title"] or "(untitled alert)", year_count=g["year"],
                    recent_count=g["recent"], number=g["number"], url=g["url"],
                    handlers=handlers.get(sig, []))
        for sig, g in groups.items() if g["recent"] >= 1 and g["year"] > year_min
    ]
    rows.sort(key=lambda r: (-r.year_count, -r.recent_count, r.title.lower()))
    return rows
