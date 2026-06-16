"""Static, non-secret configuration: regions, timezones, roster, projects, URLs.

This is the single source of truth for *who* is on the team and *where* the
dashboard reads from. Secrets (tokens, iCal URL) live only in ``secrets/*.txt``
(see ``settings.py``); nothing here is sensitive, so this file is committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

# ---------------------------------------------------------------------------
# Jira / project configuration (Assumptions in spec.md)
# ---------------------------------------------------------------------------

JIRA_BASE_URL = "https://warthogs.atlassian.net"
JIRA_ACCOUNT_EMAIL = "fernando.carrillo.castro@canonical.com"

PROJECT_ISDB = "ISDB"
PROJECT_ISREQ = "ISReq"
PROJECT_KEYS = (PROJECT_ISDB, PROJECT_ISREQ)

# Jira boards to read the active sprint ("pulse") from, per project. Pinned
# because board discovery via projectKeyOrId is unreliable here (ISDB returns a
# kanban board first; ISReq's board isn't returned by the project filter).
PROJECT_BOARDS: dict[str, int] = {PROJECT_ISDB: 1400, PROJECT_ISREQ: 11304}

# How far back a refresh collects activity (Jira "updated"/touches + PagerDuty
# incidents). Defaults to a week so a refresh covers the current pulse week
# (Mon→today); override with STANDUP_WINDOW_DAYS (e.g. "1" for fast test refreshes).
FETCH_WINDOW_DAYS = int(os.environ.get("STANDUP_WINDOW_DAYS", "7"))

# Pulse calendar (#93): a pulse is a 2-week cycle. Each anchor pins a Monday
# (week 1, day 1) to its pulse number; the counts window is clamped to the
# current pulse so closes rolled forward from a prior pulse aren't recounted.
# Add a new anchor to renumber a year (e.g. 2027 Pulse 1, week 1).
PULSE_LENGTH_DAYS = 14
PULSE_ANCHORS: tuple[tuple[date, int], ...] = (
    (date(2026, 1, 5), 1),   # Mon Jan 5 2026 = Pulse 1, week 1 (Jan 5 + 11*14 = Jun 8)
    (date(2026, 6, 8), 12),  # Mon Jun 8 2026 = Pulse 12, week 1
)

# Hard floor for the PagerDuty incidents window: never request incidents from
# before this instant, regardless of the fetch window. Set to Monday June 08 so
# the week-starting-Mon-08 numbers are collected in full (#90).
PAGERDUTY_MIN_SINCE = datetime(2026, 6, 8, tzinfo=UTC)

# PagerDuty team(s) whose incidents are relevant (the roster's "IS" squad).
# Scopes the /incidents query so a refresh fetches this team's alerts, not the
# entire organization's. Override with STANDUP_PD_TEAM_IDS (comma-separated).
PAGERDUTY_TEAM_IDS = tuple(
    t for t in os.environ.get("STANDUP_PD_TEAM_IDS", "PQ4ZG3S").split(",") if t
)

# GitHub org whose open PRs feed the "GH PRs" card line (#173). Empty disables
# the lookup (the line stays 0). Per-engineer GitHub logins live on the roster
# (``EngineerConfig.github_login``); both that and a read-only token in
# ``secrets/github_token.txt`` must be set for an engineer's count to populate.
GITHUB_ORG = os.environ.get("STANDUP_GITHUB_ORG", "canonical")

# Concurrency for the GitHub PR fetch. Each engineer needs four Search-API
# queries and that endpoint rate-limits aggressively (low primary cap + a
# burst-based secondary limit), so keep this small. Override with
# STANDUP_GITHUB_CONCURRENCY.
GITHUB_FETCH_CONCURRENCY = int(os.environ.get("STANDUP_GITHUB_CONCURRENCY", "2"))

# Server bind. Defaults to loopback (single-user, localhost-only per FR-011).
# Set STANDUP_HOST=0.0.0.0 to expose the dashboard on the LAN (no auth — only
# do this on a trusted network), and STANDUP_PORT to change the port.
HOST = os.environ.get("STANDUP_HOST", "127.0.0.1")
PORT = int(os.environ.get("STANDUP_PORT", "8765"))

# ---------------------------------------------------------------------------
# Regions (FR-002) — IANA timezones
# ---------------------------------------------------------------------------

REGION_TIMEZONES: dict[str, str] = {
    "AMER": "America/Mexico_City",
    "APAC": "Australia/Sydney",
    "EMEA": "Europe/Paris",
}
REGION_KEYS = tuple(REGION_TIMEZONES.keys())

# Follow-the-sun ticket attribution: a ticket belongs to the region whose
# working-hours window (in UTC) contains its *creation* time — independent of
# who later gets assigned. The three windows tile the full 24h day, so every
# ticket maps to exactly one region. Boundaries are fixed UTC (≈ each region's
# 09:00–17:00 local), so they drift ~1h vs local time across DST. Retune here.
REGION_CREATION_WINDOWS_UTC: dict[str, tuple[int, int]] = {
    "EMEA": (7, 15),   # 07:00–15:00 UTC  (Paris  ~09:00–17:00)
    "AMER": (15, 23),  # 15:00–23:00 UTC  (Mexico ~09:00–17:00)
    "APAC": (23, 7),   # 23:00–07:00 UTC  (Sydney ~09:00–17:00, wraps midnight)
}


@dataclass(frozen=True)
class EngineerConfig:
    name: str
    email: str
    region_keys: tuple[str, ...]
    is_manager: bool = False
    is_global: bool = False
    # Short names used in the manager's spreadsheet headers, for schedule paste
    # (#71). Matched case-insensitively alongside email/full-name/first-name.
    aliases: tuple[str, ...] = ()
    # GitHub login for the "GH PRs" card line (#173); empty = not mapped yet, so
    # that engineer's open-PR count stays 0.
    github_login: str = ""


@dataclass(frozen=True)
class RegionConfig:
    key: str
    timezone: str
    manager_email: str
    member_emails: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Roster (FR-003/004/005). Fernando manages AMER + APAC; Javier manages EMEA.
# All four managers (Fernando, Javier, Kristofer, Alexandre Micouleau) are shown
# under a dedicated "Management" group and excluded from region counts (#72).
# ---------------------------------------------------------------------------

_SEED_ROSTER: tuple[EngineerConfig, ...] = (
    # AMER
    EngineerConfig("Fernando Carrillo", "fernando.carrillo.castro@canonical.com",
                   ("AMER", "APAC"), is_manager=True, github_login="fercc17"),
    EngineerConfig("Alexandre Gomes", "alexandre.gomes@canonical.com", ("AMER",),
                   aliases=("Alejdg", "Alex G"), github_login="alejdg"),
    EngineerConfig("Colin Misare", "colin.misare@canonical.com", ("AMER",),
                   github_login="cmisare"),
    EngineerConfig("Matheus Carvalho", "matheus.carvalho@canonical.com", ("AMER",),
                   aliases=("Matt",), github_login="mcarvalhor"),
    EngineerConfig("Nikolaos Sakkos", "nikolaos.sakkos@canonical.com", ("AMER",),
                   aliases=("Nick", "Niko"), github_login="nsakkos"),
    EngineerConfig("Alex Lukens", "alex.lukens@canonical.com", ("AMER",),
                   aliases=("Alex L",), github_login="alexdlukens-canonical"),
    EngineerConfig("Afif Refrizal", "afif.refrizal@canonical.com", ("AMER",),
                   github_login="afiffahreza"),
    # APAC
    EngineerConfig("James Simpson", "james.simpson@canonical.com", ("APAC",),
                   github_login="jsimps"),
    EngineerConfig("Loic Gomez", "loic.gomez@canonical.com", ("APAC",),
                   github_login="kot0dama"),
    EngineerConfig("Paul Collins", "paul.collins@canonical.com", ("APAC",),
                   github_login="vmpjdc"),
    EngineerConfig("Haw Loeung", "haw.loeung@canonical.com", ("APAC",),
                   github_login="hloeung"),
    EngineerConfig("Barry Price", "barry.price@canonical.com", ("APAC",),
                   github_login="barryprice"),
    # EMEA
    EngineerConfig("Javier Arregui", "javier.arregui@canonical.com", ("EMEA",),
                   is_manager=True, github_login="javier-arregui"),
    EngineerConfig("Benjamin Allot", "benjamin.allot@canonical.com", ("EMEA",),
                   github_login="ben-ballot"),
    EngineerConfig("Gianluca Perna", "gianluca.perna@canonical.com", ("EMEA",),
                   github_login="gianlucaperna"),
    EngineerConfig("Christos Betzelos", "christos.betzelos@canonical.com", ("EMEA",),
                   github_login="chrisbetze"),
    EngineerConfig("Giorgos Apostolopoulos", "giorgos.apostolopoulos@canonical.com", ("EMEA",),
                   github_login="joj0s"),
    EngineerConfig("Junien Fridrick", "junien.fridrick@canonical.com", ("EMEA",),
                   github_login="axinojolais"),
    EngineerConfig("Laurent Sesques", "laurent.sesques@canonical.com", ("EMEA",),
                   github_login="sajoupa"),
    # Global management (visible but excluded from counts — FR-004)
    EngineerConfig("Kristofer Tingdahl", "kristofer.tingdahl@canonical.com", (),
                   is_global=True, github_login="tingdahl"),
    EngineerConfig("Alexandre Micouleau", "alexandre.micouleau@canonical.com", (),
                   is_global=True, github_login="alexmicouleau"),
)


def _build_regions(roster: tuple[EngineerConfig, ...]) -> dict[str, RegionConfig]:
    managers = {
        "AMER": "fernando.carrillo.castro@canonical.com",
        "APAC": "fernando.carrillo.castro@canonical.com",
        "EMEA": "javier.arregui@canonical.com",
    }
    regions: dict[str, RegionConfig] = {}
    for key, tz in REGION_TIMEZONES.items():
        # Managers are grouped under "Management", not their regions (#72), so
        # they're not region members and are excluded from region counts.
        members = tuple(
            e.email for e in roster if key in e.region_keys and not e.is_manager
        )
        regions[key] = RegionConfig(
            key=key, timezone=tz, manager_email=managers[key], member_emails=members
        )
    return regions


# The live roster + its derived indexes. Starts from the seed and is rebuilt at
# runtime from DB overrides (added engineers / region moves, #16). Call sites use
# config.ROSTER / REGIONS / ENGINEERS_BY_EMAIL (attribute access), so rebuilding
# these module globals updates everyone.
ROSTER: tuple[EngineerConfig, ...] = _SEED_ROSTER
REGIONS: dict[str, RegionConfig] = {}
ENGINEERS_BY_EMAIL: dict[str, EngineerConfig] = {}


def _set_roster(roster: tuple[EngineerConfig, ...]) -> None:
    global ROSTER, REGIONS, ENGINEERS_BY_EMAIL
    ROSTER = tuple(roster)
    REGIONS = _build_regions(ROSTER)
    ENGINEERS_BY_EMAIL = {e.email: e for e in ROSTER}


def rebuild_roster(
    additions: tuple[EngineerConfig, ...] = (),
    region_overrides: dict[str, str] | None = None,
) -> None:
    """Rebuild the live roster from the seed plus DB-backed overrides (#16).

    ``additions`` are engineers added via the UI; ``region_overrides`` moves an
    engineer (by email) to a different region. Management members aren't moved.
    """
    region_overrides = region_overrides or {}

    def _move(e: EngineerConfig) -> EngineerConfig:
        new = region_overrides.get(e.email)
        if new and new in REGION_TIMEZONES and not (e.is_manager or e.is_global):
            return replace(e, region_keys=(new,))
        return e

    out = [_move(e) for e in _SEED_ROSTER]
    seen = {e.email for e in out}
    for a in additions:
        if a.email not in seen:
            out.append(_move(a))
            seen.add(a.email)
    _set_roster(tuple(out))


_set_roster(_SEED_ROSTER)


def jira_browse_url(issue_key: str) -> str:
    """Public Jira URL that opens a single issue (FR: clickable ticket links)."""
    return f"{JIRA_BASE_URL}/browse/{issue_key}"


def region_timezone(region_key: str) -> str:
    return REGION_TIMEZONES[region_key]


def engineers_in_region(region_key: str) -> list[EngineerConfig]:
    return [e for e in ROSTER if region_key in e.region_keys]


def global_engineers() -> list[EngineerConfig]:
    return [e for e in ROSTER if e.is_global]


def management_engineers() -> list[EngineerConfig]:
    """Regional managers + global management, shown under one 'Management' group.

    Treated like the old Global group (#72): excluded from every region's member
    list and from all counts; displayed separately for visibility.
    """
    return [e for e in ROSTER if e.is_manager or e.is_global]


def is_counted(engineer: EngineerConfig) -> bool:
    """True for engineers who contribute to region/alert counts (not management)."""
    return not engineer.is_manager and not engineer.is_global


def all_roster_emails() -> list[str]:
    return [e.email for e in ROSTER]


def github_logins() -> dict[str, str]:
    """email → GitHub login for every roster member that has one (#173)."""
    return {e.email: e.github_login for e in ROSTER if e.github_login}


def seed_roster_emails() -> list[str]:
    """The curated seed roster's emails — used for the hard PagerDuty identity
    gate so ad-hoc UI additions (#16) can never block startup."""
    return [e.email for e in _SEED_ROSTER]


def primary_region_for(email: str) -> str | None:
    """The region whose timezone governs an engineer's 'today' / override expiry.

    A multi-region manager uses their first listed region as the default tz
    anchor; per-region chip display still resolves role in each region's tz.
    """
    eng = ENGINEERS_BY_EMAIL.get(email)
    if not eng or not eng.region_keys:
        return None
    return eng.region_keys[0]


def region_for_creation(created: datetime) -> str:
    """Region that owns a ticket created at ``created`` (follow-the-sun).

    Attribution is purely by UTC hour-of-day per ``REGION_CREATION_WINDOWS_UTC``,
    independent of the assignee. The windows tile the day, so this always returns
    a region. A naive ``created`` is treated as UTC.
    """
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    h = created.astimezone(UTC).hour
    for region, (start, end) in REGION_CREATION_WINDOWS_UTC.items():
        if start < end:
            if start <= h < end:
                return region
        elif h >= start or h < end:  # window wraps midnight
            return region
    return REGION_KEYS[0]  # unreachable: windows tile the full 24h
