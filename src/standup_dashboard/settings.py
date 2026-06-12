"""Secrets loading + startup validation (FR-029/030, FR-005a).

Secrets are read only from plain-text files under ``secrets/``. A missing or
empty file is a blocking *setup error* that names the expected file; the web
layer renders a setup page instead of the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_SECRETS_DIR = Path("secrets")

JIRA_TOKEN_FILE = "jira_token.txt"
PAGERDUTY_TOKEN_FILE = "pagerduty_token.txt"
PAGERDUTY_ICAL_URL_FILE = "pagerduty_ical_url.txt"


class SetupError(Exception):
    """Blocking configuration problem surfaced as a setup page (not a 500)."""

    def __init__(self, message: str, *, missing_file: str | None = None,
                 unmatched_engineers: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.missing_file = missing_file
        self.unmatched_engineers = unmatched_engineers or []


@dataclass(frozen=True)
class Secrets:
    jira_token: str
    pagerduty_token: str
    pagerduty_ical_url: str


def _read_secret(secrets_dir: Path, filename: str) -> str:
    path = secrets_dir / filename
    if not path.exists():
        raise SetupError(
            f"Required secret file is missing: secrets/{filename}. "
            f"Copy secrets.example/ into secrets/ and fill in the value.",
            missing_file=f"secrets/{filename}",
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SetupError(
            f"Required secret file is empty: secrets/{filename}.",
            missing_file=f"secrets/{filename}",
        )
    return value


def load_secrets(secrets_dir: str | Path = DEFAULT_SECRETS_DIR) -> Secrets:
    """Load all three secrets, raising SetupError naming the first bad file."""
    d = Path(secrets_dir)
    return Secrets(
        jira_token=_read_secret(d, JIRA_TOKEN_FILE),
        pagerduty_token=_read_secret(d, PAGERDUTY_TOKEN_FILE),
        pagerduty_ical_url=_read_secret(d, PAGERDUTY_ICAL_URL_FILE),
    )
