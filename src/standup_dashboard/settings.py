"""Secrets loading + startup validation (FR-029/030, FR-005a).

Secrets are read from the environment first, then from plain-text files under
``secrets/``. Env-first lets the Kubernetes 12-factor charm inject them as
environment variables (from a Juju secret); the file fallback keeps local
``python -m standup_dashboard`` working. A required secret that's absent from
both is a blocking *setup error* that names the expected source; the web layer
renders a setup page instead of the dashboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SECRETS_DIR = Path("secrets")

JIRA_TOKEN_FILE = "jira_token.txt"
PAGERDUTY_TOKEN_FILE = "pagerduty_token.txt"
PAGERDUTY_ICAL_URL_FILE = "pagerduty_ical_url.txt"
# Optional: a read-only GitHub token enables the "GH PRs" card line (#173). Its
# absence never blocks startup — the line just stays at 0.
GITHUB_TOKEN_FILE = "github_token.txt"
# Optional: a Tempo API token (Worklogs:View scope) makes worklog time attribute
# to the real logger via the Tempo API instead of the ticket's assignee. Absent =
# fall back to the Jira-worklog assignee proxy; never blocks startup (#tempo-worklogs).
TEMPO_TOKEN_FILE = "tempo_token.txt"

# Env var names per secret, tried in order before the file fallback. The charm
# binds a Juju secret to a config option of type ``secret`` named ``secrets``, so
# each key ``k`` surfaces as ``APP_SECRETS_K``. ``APP_*`` covers a plain config
# mapping and ``STANDUP_*`` is the local-dev alias.
JIRA_TOKEN_ENV = ("APP_SECRETS_JIRA_TOKEN", "APP_JIRA_TOKEN", "STANDUP_JIRA_TOKEN")
PAGERDUTY_TOKEN_ENV = (
    "APP_SECRETS_PAGERDUTY_TOKEN", "APP_PAGERDUTY_TOKEN", "STANDUP_PAGERDUTY_TOKEN")
PAGERDUTY_ICAL_URL_ENV = (
    "APP_SECRETS_PAGERDUTY_ICAL_URL", "APP_PAGERDUTY_ICAL_URL", "STANDUP_PAGERDUTY_ICAL_URL")
GITHUB_TOKEN_ENV = (
    "APP_SECRETS_GITHUB_TOKEN", "APP_GITHUB_TOKEN", "STANDUP_GITHUB_TOKEN")
TEMPO_TOKEN_ENV = (
    "APP_SECRETS_TEMPO_TOKEN", "APP_TEMPO_TOKEN", "STANDUP_TEMPO_TOKEN")


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
    github_token: str | None = None  # optional — gates the GH PRs line (#173)
    tempo_token: str | None = None  # optional — gates real-logger worklogs (#tempo-worklogs)


def _from_env(env_names: tuple[str, ...]) -> str | None:
    """First non-empty value among ``env_names``, stripped, or None."""
    for name in env_names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _read_optional_secret(
    secrets_dir: Path, env_names: tuple[str, ...], filename: str
) -> str | None:
    """Read a secret that may be absent (env first, then file) — never raises."""
    from_env = _from_env(env_names)
    if from_env:
        return from_env
    path = secrets_dir / filename
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _read_secret(secrets_dir: Path, env_names: tuple[str, ...], filename: str) -> str:
    """Read a required secret (env first, then file); SetupError if both missing."""
    from_env = _from_env(env_names)
    if from_env:
        return from_env
    path = secrets_dir / filename
    if not path.exists():
        raise SetupError(
            f"Required secret is missing: set ${env_names[0]} or secrets/{filename}. "
            f"Copy secrets.example/ into secrets/ and fill in the value.",
            missing_file=f"secrets/{filename}",
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SetupError(
            f"Required secret is empty: ${env_names[0]} / secrets/{filename}.",
            missing_file=f"secrets/{filename}",
        )
    return value


def load_secrets(secrets_dir: str | Path = DEFAULT_SECRETS_DIR) -> Secrets:
    """Load all secrets (env first, then ``secrets/*.txt``), raising SetupError
    naming the first required secret that's absent from both sources."""
    d = Path(secrets_dir)
    return Secrets(
        jira_token=_read_secret(d, JIRA_TOKEN_ENV, JIRA_TOKEN_FILE),
        pagerduty_token=_read_secret(d, PAGERDUTY_TOKEN_ENV, PAGERDUTY_TOKEN_FILE),
        pagerduty_ical_url=_read_secret(d, PAGERDUTY_ICAL_URL_ENV, PAGERDUTY_ICAL_URL_FILE),
        github_token=_read_optional_secret(d, GITHUB_TOKEN_ENV, GITHUB_TOKEN_FILE),
        tempo_token=_read_optional_secret(d, TEMPO_TOKEN_ENV, TEMPO_TOKEN_FILE),
    )
