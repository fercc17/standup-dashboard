#!/usr/bin/env python3
"""PyCharm-friendly entry point.

Click the green **Run** button on this file to start the dashboard; click the
red **Stop** button to halt it. Equivalent to ``python -m standup_dashboard``.

For background start/stop from a terminal, use ``scripts/app.sh``.
"""

from __future__ import annotations

from standup_dashboard.__main__ import main

if __name__ == "__main__":
    main()
