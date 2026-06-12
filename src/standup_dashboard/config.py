"""Static, non-secret configuration: regions, timezones, roster, projects, URLs.

This is the single source of truth for *who* is on the team and *where* the
dashboard reads from. Secrets (tokens, iCal URL) live only in ``secrets/*.txt``
(see ``settings.py``); nothing here is sensitive, so this file is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Jira / project configuration (Assumptions in spec.md)
# ---------------------------------------------------------------------------

JIRA_BASE_URL = "https://warthogs.atlassian.net"
JIRA_ACCOUNT_EMAIL = "fernando.carrillo.castro@canonical.com"

PROJECT_ISDB = "ISDB"
PROJECT_ISREQ = "ISReq"
PROJECT_KEYS = (PROJECT_ISDB, PROJECT_ISREQ)

# Server bind (quickstart.md)
HOST = "127.0.0.1"
PORT = 8765

# ---------------------------------------------------------------------------
# Regions (FR-002) — IANA timezones
# ---------------------------------------------------------------------------

REGION_TIMEZONES: dict[str, str] = {
    "AMER": "America/Mexico_City",
    "APAC": "Australia/Sydney",
    "EMEA": "Europe/Paris",
}
REGION_KEYS = tuple(REGION_TIMEZONES.keys())


@dataclass(frozen=True)
class EngineerConfig:
    name: str
    email: str
    region_keys: tuple[str, ...]
    is_manager: bool = False
    is_global: bool = False


@dataclass(frozen=True)
class RegionConfig:
    key: str
    timezone: str
    manager_email: str
    member_emails: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Roster (FR-003/004/005). Fernando manages AMER + APAC; Javier manages EMEA.
# Kristofer and Alexandre Micouleau are global management (shown under
# "Global", excluded from all counts — FR-004).
# ---------------------------------------------------------------------------

ROSTER: tuple[EngineerConfig, ...] = (
    # AMER
    EngineerConfig("Fernando Carrillo Castro", "fernando.carrillo.castro@canonical.com",
                   ("AMER", "APAC"), is_manager=True),
    EngineerConfig("Alexandre Gomes", "alexandre.gomes@canonical.com", ("AMER",)),
    EngineerConfig("Colin Misare", "colin.misare@canonical.com", ("AMER",)),
    EngineerConfig("Matheus Carvalho", "matheus.carvalho@canonical.com", ("AMER",)),
    EngineerConfig("Nikolaos Sakkos", "nikolaos.sakkos@canonical.com", ("AMER",)),
    EngineerConfig("Alex Lukens", "alex.lukens@canonical.com", ("AMER",)),
    EngineerConfig("Afif Refrizal", "afif.refrizal@canonical.com", ("AMER",)),
    # APAC
    EngineerConfig("James Simpson", "james.simpson@canonical.com", ("APAC",)),
    EngineerConfig("Loic Gomez", "loic.gomez@canonical.com", ("APAC",)),
    EngineerConfig("Paul Collins", "paul.collins@canonical.com", ("APAC",)),
    EngineerConfig("Haw Loeung", "haw.loeung@canonical.com", ("APAC",)),
    EngineerConfig("Barry Price", "barry.price@canonical.com", ("APAC",)),
    # EMEA
    EngineerConfig("Javier Arregui", "javier.arregui@canonical.com", ("EMEA",), is_manager=True),
    EngineerConfig("Benjamin Allot", "benjamin.allot@canonical.com", ("EMEA",)),
    EngineerConfig("Gianluca Perna", "gianluca.perna@canonical.com", ("EMEA",)),
    EngineerConfig("Christos Betzelos", "christos.betzelos@canonical.com", ("EMEA",)),
    EngineerConfig("Giorgos Apostolopoulos", "giorgos.apostolopoulos@canonical.com", ("EMEA",)),
    EngineerConfig("Junien Fridrick", "junien.fridrick@canonical.com", ("EMEA",)),
    EngineerConfig("Laurent Sesques", "laurent.sesques@canonical.com", ("EMEA",)),
    # Global management (visible but excluded from counts — FR-004)
    EngineerConfig("Kristofer Tingdahl", "kristofer.tingdahl@canonical.com", (), is_global=True),
    EngineerConfig("Alexandre Micouleau", "alexandre.micouleau@canonical.com", (), is_global=True),
)


def _build_regions() -> dict[str, RegionConfig]:
    managers = {
        "AMER": "fernando.carrillo.castro@canonical.com",
        "APAC": "fernando.carrillo.castro@canonical.com",
        "EMEA": "javier.arregui@canonical.com",
    }
    regions: dict[str, RegionConfig] = {}
    for key, tz in REGION_TIMEZONES.items():
        members = tuple(e.email for e in ROSTER if key in e.region_keys)
        regions[key] = RegionConfig(
            key=key, timezone=tz, manager_email=managers[key], member_emails=members
        )
    return regions


REGIONS: dict[str, RegionConfig] = _build_regions()

# Email → EngineerConfig index (canonical identity, FR-005a)
ENGINEERS_BY_EMAIL: dict[str, EngineerConfig] = {e.email: e for e in ROSTER}


def region_timezone(region_key: str) -> str:
    return REGION_TIMEZONES[region_key]


def engineers_in_region(region_key: str) -> list[EngineerConfig]:
    return [e for e in ROSTER if region_key in e.region_keys]


def global_engineers() -> list[EngineerConfig]:
    return [e for e in ROSTER if e.is_global]


def all_roster_emails() -> list[str]:
    return [e.email for e in ROSTER]


def primary_region_for(email: str) -> str | None:
    """The region whose timezone governs an engineer's 'today' / override expiry.

    A multi-region manager uses their first listed region as the default tz
    anchor; per-region chip display still resolves role in each region's tz.
    """
    eng = ENGINEERS_BY_EMAIL.get(email)
    if not eng or not eng.region_keys:
        return None
    return eng.region_keys[0]
