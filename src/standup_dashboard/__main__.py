"""Entry point: ``python -m standup_dashboard`` launches uvicorn on localhost."""

from __future__ import annotations

import uvicorn

from . import config
from .app import configure_logging, create_app


def main() -> None:
    configure_logging()
    app = create_app()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
