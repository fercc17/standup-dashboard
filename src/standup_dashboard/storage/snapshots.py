"""Append-only raw JSON snapshot writer (FR-028, data-model.md §2).

Each fetch writes full-fidelity payloads under
``data/snapshots/<fetched_at>/`` and is never pruned. This preserves
everything the APIs returned, independent of the SQLite schema.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOTS_DIR = Path("data/snapshots")


def _stamp(dt: datetime) -> str:
    # Filesystem-safe, sortable timestamp directory name.
    return dt.strftime("%Y%m%dT%H%M%SZ") if dt.tzinfo is None else dt.strftime("%Y%m%dT%H%M%S%z")


class SnapshotWriter:
    def __init__(self, base_dir: str | Path = DEFAULT_SNAPSHOTS_DIR):
        self.base_dir = Path(base_dir)

    def write(self, fetched_at: datetime, payloads: dict[str, Any]) -> str:
        """Write each named payload as a file in a per-fetch directory.

        ``payloads`` maps a filename (e.g. ``jira_isdb.json`` or ``oncall.ics``)
        to either a JSON-serialisable object or a raw string. Returns the
        directory path (stored on the fetch_snapshot row).
        """
        target = self.base_dir / _stamp(fetched_at)
        target.mkdir(parents=True, exist_ok=True)
        for filename, payload in payloads.items():
            path = target / filename
            if filename.endswith(".json"):
                path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            else:
                path.write_text(
                    payload if isinstance(payload, str) else json.dumps(payload, default=str),
                    encoding="utf-8",
                )
        return str(target)
