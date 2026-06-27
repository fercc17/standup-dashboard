"""Static, non-secret configuration: regions, timezones, roster, projects, URLs.

This is the single source of truth for *who* is on the team and *where* the
dashboard reads from. Secrets (tokens, iCal URL) live only in ``secrets/*.txt``
(see ``settings.py``); nothing here is sensitive, so this file is committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from urllib.parse import quote


def _env(*names: str, default: str = "") -> str:
    """First set environment variable among ``names``, else ``default``.

    Lets one setting be read under multiple names: the 12-factor charm exposes
    config options as ``APP_*`` env vars, while local dev / the original app use
    ``STANDUP_*``. List the charm name first so it wins when both are present.
    """
    for name in names:
        val = os.environ.get(name)
        if val is not None and val != "":
            return val
    return default


def database_dsn() -> str:
    """PostgreSQL connection string. The charm's postgresql relation injects
    ``POSTGRESQL_DB_CONNECT_STRING``; locally fall back to ``STANDUP_DB_DSN`` or
    a sensible default so ``python -m standup_dashboard`` works against a local PG.
    """
    from .storage.db import DEFAULT_DSN
    return _env("POSTGRESQL_DB_CONNECT_STRING", "STANDUP_DB_DSN", default=DEFAULT_DSN)


def _apply_proxy_env() -> None:
    """Route outbound traffic through an HTTP(S) proxy when one is configured.

    All external calls (Jira/PagerDuty/GitHub/calendar) use httpx with
    ``trust_env=True``, so they honour the standard ``HTTP_PROXY`` /
    ``HTTPS_PROXY`` / ``NO_PROXY`` env vars. The charm exposes proxy config
    options as ``APP_*_PROXY``; copy those onto the standard names (without
    clobbering an explicitly-set standard var) so a single config drives every
    client. Runs at import, before any httpx client is built.
    """
    for std, names in (
        ("HTTP_PROXY", ("APP_HTTP_PROXY", "STANDUP_HTTP_PROXY")),
        ("HTTPS_PROXY", ("APP_HTTPS_PROXY", "STANDUP_HTTPS_PROXY")),
        ("NO_PROXY", ("APP_NO_PROXY", "STANDUP_NO_PROXY")),
    ):
        val = _env(*names)
        if val and not os.environ.get(std) and not os.environ.get(std.lower()):
            os.environ[std] = val
            os.environ[std.lower()] = val


_apply_proxy_env()


# How often the background scheduler process triggers a refresh (seconds). Retained
# for the legacy single-interval mode / cold-start; per-source cron is below.
REFRESH_INTERVAL_SECONDS = int(_env("APP_REFRESH_INTERVAL", "STANDUP_REFRESH_INTERVAL",
                                    default="1800"))

# Per-source refresh schedule (#per-source-schedule). The scheduler ticks every minute
# and fetches whichever sources are "due" — each on its own cadence, tailored to how
# fast it changes and to land fresh before the standups (Jira/PD at :13 and :58 sit just
# ahead of the :15 and :00 standups). Minutes are within every hour; iCal (on-call,
# rarely changes) runs once a day instead.
SOURCE_SCHEDULE_MINUTES: dict[str, frozenset[int]] = {
    "jira":      frozenset({13, 30, 58}),
    "pagerduty": frozenset({13, 30, 58}),
    "github":    frozenset({13, 23, 33, 44, 58}),
    "calendar":  frozenset({45}),
}
ICAL_DAILY_HOUR = 0          # iCal once a day at this UTC hour, minute 0
ALL_SOURCES = frozenset({"jira", "pagerduty", "github", "calendar", "ical"})


def due_sources(now: datetime) -> frozenset[str]:
    """Which sources are due to refresh at ``now`` (UTC), per the per-source schedule."""
    due = {s for s, mins in SOURCE_SCHEDULE_MINUTES.items() if now.minute in mins}
    if now.hour == ICAL_DAILY_HOUR and now.minute == 0:
        due.add("ical")
    return frozenset(due)

# Raw-snapshot fidelity (#snapshot-trim). Every refresh re-fetches the whole
# sprint board with ``expand=changelog`` and stores it verbatim in the write-only
# ``raw_snapshot`` table — but ~75% of a Jira snapshot's bytes are avatar URLs and
# self-links inside changelog *authors*, which nothing ever reads back (the
# normalized tables are derived at fetch time). With a 30-min scheduler plus
# on-demand refreshes that grows the JSONB store fast, so by default we trim those
# author blocks down to identity (accountId/displayName/emailAddress) + the
# transition items — ~60% smaller while keeping the changelog fully re-derivable,
# so FR-028 trend analysis and ``scripts/backfill_wip_since.py`` still work. Set
# STANDUP_RAW_SNAPSHOT_FULL=1 (charm: ``raw-snapshot-full=true``) to store verbatim
# again. See memory: raw-snapshot-changelog-trim.
RAW_SNAPSHOT_FULL = _env("APP_RAW_SNAPSHOT_FULL", "STANDUP_RAW_SNAPSHOT_FULL",
                         default="0").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Jira / project configuration (Assumptions in spec.md)
# ---------------------------------------------------------------------------

JIRA_BASE_URL = _env("APP_JIRA_BASE_URL", "STANDUP_JIRA_BASE_URL",
                     default="https://warthogs.atlassian.net")
JIRA_ACCOUNT_EMAIL = _env("APP_JIRA_ACCOUNT_EMAIL", "STANDUP_JIRA_ACCOUNT_EMAIL",
                          default="fernando.carrillo.castro@canonical.com")

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
FETCH_WINDOW_DAYS = int(_env("APP_WINDOW_DAYS", "STANDUP_WINDOW_DAYS", default="7"))

# How far back to keep re-polling an incident that is still acked-but-unresolved
# (#open-alert-persist). An open alert is ongoing work, so it stays visible until
# it resolves — even across a pulse boundary — instead of vanishing the moment a
# new pulse starts. Bounds the recheck set so an incident left open forever isn't
# polled indefinitely.
OPEN_ALERT_RECHECK_DAYS = int(
    _env("APP_OPEN_ALERT_RECHECK_DAYS", "STANDUP_OPEN_ALERT_RECHECK_DAYS", default="30"))

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
    t for t in _env("APP_PD_TEAM_IDS", "STANDUP_PD_TEAM_IDS", default="PQ4ZG3S").split(",") if t
)

# GitHub org whose open PRs feed the "GH PRs" card line (#173). Empty disables
# the lookup (the line stays 0). Per-engineer GitHub logins live on the roster
# (``EngineerConfig.github_login``); both that and a read-only token in
# ``secrets/github_token.txt`` must be set for an engineer's count to populate.
GITHUB_ORG = _env("APP_GITHUB_ORG", "STANDUP_GITHUB_ORG", default="canonical")

# Concurrency for the GitHub PR fetch. Each engineer needs four Search-API
# queries and that endpoint rate-limits aggressively (low primary cap + a
# burst-based secondary limit), so keep this small. Override with
# STANDUP_GITHUB_CONCURRENCY.
GITHUB_FETCH_CONCURRENCY = int(_env("APP_GITHUB_CONCURRENCY", "STANDUP_GITHUB_CONCURRENCY",
                                    default="2"))

# Tempo Cloud REST API base (#tempo-worklogs). v4 ``/worklogs`` carries the real
# logger (``author.accountId``) per worklog, unlike Jira's worklog endpoint which
# Tempo authors under a bot. Used only when a Tempo token is configured. Override
# with STANDUP_TEMPO_BASE_URL for Tempo Server/DC.
TEMPO_BASE_URL = _env("APP_TEMPO_BASE_URL", "STANDUP_TEMPO_BASE_URL",
                      default="https://api.tempo.io/4")

# How many days back each refresh queries Tempo worklogs, regardless of the (often
# tiny) incremental Jira window. Worklogs are routinely logged late and backdated, so
# an incremental window misses them; this lookback lets a refresh re-see a worklog
# created now but dated up to a week ago, which the createdAt touch filter then keeps
# (#tempo-backdate). Bounded so the per-refresh Tempo query stays cheap.
TEMPO_WORKLOG_LOOKBACK_DAYS = int(
    _env("APP_TEMPO_LOOKBACK_DAYS", "STANDUP_TEMPO_LOOKBACK_DAYS", default="8"))

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

# Follow-the-sun on-call handover order (#handover): the PVG/BVG duty rotates
# APAC → EMEA → AMER → APAC through the UTC day (matching the working-hours
# windows below). A PVG/BVG in one region hands over to the next region's holder
# and receives from the previous one.
HANDOVER_ORDER = ("APAC", "EMEA", "AMER")

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

# Local working-hours window [start_hour, end_hour) used to split weekend on-call
# alerts into in-hours vs off-hours (#recap-hours): an alert that fired between
# 09:00 and 17:00 in the on-call's own timezone counts as in-hours, anything else
# (evenings, nights, early mornings) as off-hours. Retune per the team's norms.
BUSINESS_HOURS_LOCAL: tuple[int, int] = (9, 17)


@dataclass(frozen=True)
class EngineerConfig:
    name: str
    email: str
    region_keys: tuple[str, ...]
    is_manager: bool = False
    is_global: bool = False
    # Marks the engineer's chip with a ★ (#star). A free-standing designation,
    # independent of the manager/global flags (which drive the Management grouping).
    starred: bool = False
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
                   starred=True, github_login="barryprice"),
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


# Saved Jira filters the open-work summary line links to — one curated filter per
# open-work category (#summary-links). Built from JIRA_BASE_URL, so a different
# Jira instance only needs new filter ids here.
JIRA_OPEN_FILTERS: dict[str, int] = {
    "highest": 39785,       # Open IS Highest
    "ps5": 39782,           # Open ps5-blockers
    "ps5_highest": 40098,   # Open ps5-blockers at Highest
    "pr_mp": 40086,         # Open PR/MPs
}


def jira_filter_url(filter_id: int) -> str:
    """Jira issue-navigator URL for a saved filter id."""
    return f"{JIRA_BASE_URL}/issues/?filter={filter_id}"


def jira_jql_url(jql: str) -> str:
    """Jira issue-navigator URL for an arbitrary JQL string (#summary-links)."""
    return f"{JIRA_BASE_URL}/issues/?jql={quote(jql)}"


def jira_sprint_board_url(project_key: str, account_id: str) -> str:
    """A project's active sprint board, filtered to one person by Jira accountId
    (#sprint-link): e.g. .../jira/software/c/projects/ISDB/boards/1400?assignee=<id>.
    accountId works for everyone, unlike an email filter (private-email engineers)."""
    board = PROJECT_BOARDS.get(project_key)
    return (f"{JIRA_BASE_URL}/jira/software/c/projects/{project_key.upper()}"
            f"/boards/{board}?assignee={quote(account_id)}")


# JQL for the "Escalated ISReq" summary item — every ISReq ticket sitting in the
# Jira ``Escalated`` workflow status (project key is uppercase ``ISREQ`` in JQL,
# though the dashboard's canonical key is ``ISReq``). The count is recomputed from
# fetched tickets each refresh; this link opens the authoritative live list.
JIRA_ESCALATED_ISREQ_JQL = (
    f'project = {PROJECT_ISREQ.upper()} AND status = "Escalated" ORDER BY updated DESC'
)


# PagerDuty web subdomain (the UI host, distinct from the api.pagerduty.com REST
# host). Used only to deep-link the "Ongoing alerts" count to the live incident
# list. Override with STANDUP_PD_SUBDOMAIN for another account.
PAGERDUTY_SUBDOMAIN = _env("APP_PD_SUBDOMAIN", "STANDUP_PD_SUBDOMAIN", default="canonical")


def pagerduty_open_incidents_url() -> str:
    """PagerDuty UI link to the team's still-open (triggered + acknowledged)
    incidents — what the 'Ongoing alerts' summary count reflects (#summary-links)."""
    url = (f"https://{PAGERDUTY_SUBDOMAIN}.pagerduty.com/incidents"
           "?status=triggered,acknowledged")
    if PAGERDUTY_TEAM_IDS:
        url += "&team_ids=" + ",".join(PAGERDUTY_TEAM_IDS)
    return url


# Whether to pull per-engineer calendar free/busy (#cal). The public Google iCal
# URL is derivable from the email, but only resolves for calendars the person has
# made public (others 404 fast and are skipped). On by default; set
# STANDUP_CALENDAR=0 to disable (e.g. to skip the extra fetch entirely).
CALENDAR_ENABLED = _env("APP_CALENDAR", "STANDUP_CALENDAR",
                        default="1").lower() not in ("0", "false", "no")


def calendar_ical_url(email: str) -> str:
    """Public Google iCal (free/busy) feed URL for an engineer's calendar (#cal).

    Resolves to real data only when that person has made their calendar public
    ("see all" or free/busy); otherwise the fetch 404s and the card shows no
    calendar data. No secret/token needed — derived purely from the email."""
    return f"https://calendar.google.com/calendar/ical/{quote(email)}/public/basic.ics"


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
