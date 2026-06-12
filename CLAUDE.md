<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan at
`specs/001-sre-standup-dashboard/plan.md` (with design detail in the sibling
`research.md`, `data-model.md`, `contracts/`, and `quickstart.md`).

Stack: Python 3.12 · FastAPI + uvicorn · Jinja2 + HTMX (no build) · httpx ·
SQLite + raw JSON snapshots · icalendar · zoneinfo · uv · pytest. Single-user
local web app; strictly read-only toward Jira & PagerDuty; secrets live only in
gitignored `secrets/*.txt`.
<!-- SPECKIT END -->
