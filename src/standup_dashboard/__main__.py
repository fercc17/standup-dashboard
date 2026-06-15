"""Entry point: ``python -m standup_dashboard`` launches uvicorn on localhost.

Auto-reload is on by default so edits to the Python/CSS/JS show up without a
manual restart; set ``STANDUP_RELOAD=0`` to disable it (e.g. LAN exposure).
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from . import config
from .app import configure_logging, create_app

_PACKAGE_DIR = Path(__file__).parent


def main() -> None:
    configure_logging()
    reload = os.environ.get("STANDUP_RELOAD", "1").lower() not in ("0", "false", "no")
    if reload:
        # Reload needs an import string + factory; watch only the package source
        # (so data/ and secrets/ writes never trigger a reload). Watching *.css/
        # *.js too means a static-asset edit restarts the worker, which refreshes
        # the per-start static_version cache-buster (app.py) — no hard refresh.
        uvicorn.run(
            "standup_dashboard.app:create_app",
            factory=True,
            host=config.HOST,
            port=config.PORT,
            reload=True,
            reload_dirs=[str(_PACKAGE_DIR)],
            reload_includes=["*.py", "*.css", "*.js"],
            log_level="info",
        )
    else:
        uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
