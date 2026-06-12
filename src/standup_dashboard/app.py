"""FastAPI app factory + startup checks + Jinja2/static mounts (T012).

Single-user, localhost-only, no authentication layer (FR-011). On startup the
app attempts to load secrets and validate the roster→PagerDuty identity gate
(FR-005a); any failure is stored as ``app.state.setup_error`` and the web layer
serves a blocking setup page instead of the dashboard.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .settings import Secrets, SetupError, load_secrets
from .storage.db import Database
from .storage.snapshots import SnapshotWriter

logger = logging.getLogger("standup_dashboard")

_WEB_DIR = Path(__file__).parent / "web"
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"


def configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def validate_startup(secrets: Secrets) -> None:
    """Run blocking startup validations. Raises SetupError on failure.

    Phase 2 validates secrets presence (done by load_secrets). The roster→
    PagerDuty identity gate (FR-005a) is wired in T021 once the PagerDuty
    client exists; see ``services.identity``.
    """
    from .services.identity import validate_identities  # local import: lands in US1

    validate_identities(secrets)


class RefreshState:
    """Tracks an in-flight background refresh (single-user, in-memory)."""

    def __init__(self) -> None:
        self.running = False
        self.error: str | None = None


class AppState:
    def __init__(self, db: Database, snapshots: SnapshotWriter,
                 secrets: Secrets | None, setup_error: SetupError | None):
        self.db = db
        self.snapshots = snapshots
        self.secrets = secrets
        self.setup_error = setup_error
        self.refresh = RefreshState()


def create_app(
    *,
    db_path: str | Path = "data/dashboard.db",
    secrets_dir: str | Path = "secrets",
    snapshots_dir: str | Path = "data/snapshots",
    run_startup_validation: bool = True,
) -> FastAPI:
    configure_logging()
    app = FastAPI(title="IS SRE Standup Dashboard", docs_url=None, redoc_url=None)

    db = Database(db_path)
    snapshots = SnapshotWriter(snapshots_dir)

    secrets: Secrets | None = None
    setup_error: SetupError | None = None
    try:
        secrets = load_secrets(secrets_dir)
        if run_startup_validation:
            validate_startup(secrets)
    except SetupError as exc:
        logger.warning("Startup blocked by setup error: %s", exc.message)
        setup_error = exc

    app.state.ctx = AppState(db, snapshots, secrets, setup_error)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.config = config

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from .web.routes import router
    app.include_router(router)

    logger.info("App created (setup_error=%s)", bool(setup_error))
    return app
