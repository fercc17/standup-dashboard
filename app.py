"""Module-level ASGI app for production servers and the 12-factor rock.

The rockcraft ``fastapi-framework`` extension discovers a module-level ``app``
object at the project root — it does not call an application factory — so this
thin shim exposes one. Local development still uses the ``create_app()`` factory
via ``python -m standup_dashboard`` (see ``__main__.py``).

The package lives under ``src/``; the rock copies ``src/`` alongside this file
but doesn't pip-install the project, so put ``src`` on the path before importing
(a no-op locally, where ``uv`` installs the project into the venv).

Run directly with e.g. ``uvicorn app:app``.
"""

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from standup_dashboard.app import create_app  # noqa: E402  (after sys.path setup)

app = create_app()
