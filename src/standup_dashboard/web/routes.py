"""FastAPI routes (contracts/internal-web.md).

Serves the dashboard shell + setup page (Phase 2) and the US1 surface: the
full page, manual refresh, and additive engineer detail panels. Later phases
add schedule/toggle routes and the counts table. No route mutates Jira or
PagerDuty (FR-027).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import config
from ..domain.models import WEEKDAYS, FetchSnapshot, Role
from ..services import schedule
from ..services.fetch import run_fetch
from . import presenters

logger = logging.getLogger("standup_dashboard.web")

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.ctx


def _templates(request: Request):
    return request.app.state.templates


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_regions(values: list[str]) -> list[str]:
    """Validate + dedupe requested regions; default to the first region."""
    if not values:
        return [config.REGION_KEYS[0]]
    out: list[str] = []
    for v in values:
        if v not in config.REGIONS:
            raise ValueError(v)
        if v not in out:
            out.append(v)
    return out


def _region_links(selected: list[str]) -> list[dict]:
    """Toggle links for the region buttons (multi-select, FR-002/005)."""
    links: list[dict] = []
    for r in config.REGION_KEYS:
        new = [x for x in selected if x != r] if r in selected else [*selected, r]
        query = "&".join(f"regions={x}" for x in new)
        links.append({"key": r, "href": f"/?{query}" if query else "/", "active": r in selected})
    return links


def _fmt_fetch(latest: FetchSnapshot, regions: list[str], now: datetime) -> str:
    tz = config.REGIONS[regions[0]].timezone if regions else "UTC"
    local = latest.fetched_at.astimezone(ZoneInfo(tz))
    return f"Last fetch: {local:%a %d %b %H:%M %Z}"


def render_setup(request: Request, status_code: int = 200) -> HTMLResponse:
    error = _ctx(request).setup_error
    return _templates(request).TemplateResponse(
        request, "setup.html", {"error": error}, status_code=status_code
    )


def _dashboard_context(request: Request, selected_regions: list[str], now: datetime) -> dict:
    ctx = _ctx(request)
    db = ctx.db
    context: dict = {
        "regions": config.REGION_KEYS,
        "selected_regions": selected_regions,
        "region_links": _region_links(selected_regions),
        "strict_mode": schedule.get_strict_mode(db),
        "strict_visible": presenters.any_bvg_today(db, selected_regions, now),
        "counts_rows": [],         # wired in US3 (T040)
        "banner": None,
        "ready": False,
        "chip_groups": [],
        "global_chips": [],
        "last_fetch_label": "No fetch yet",
    }

    latest = db.latest_fetch()
    if latest is None:
        return context

    # Last-good fallback (US6/FR-028): if the latest fetch's primary source
    # (Jira) failed, render the most recent fetch where it succeeded.
    display = latest
    if not latest.jira_ok:
        good = db.latest_good_fetch()
        if good is not None:
            display = good

    data = presenters.load_fetch_data(db, display.fetched_at, display.id)
    chip_groups, global_chips = presenters.build_chip_groups(db, data, selected_regions, now)
    context.update(
        ready=True,
        chip_groups=chip_groups,
        global_chips=global_chips,
        counts_rows=presenters.build_counts(data, selected_regions, now),
        last_fetch_label=_fmt_fetch(display, selected_regions, now),
    )

    failed = [
        name for name, ok in (
            ("Jira", latest.jira_ok),
            ("PagerDuty", latest.pagerduty_ok),
            ("on-call iCal", latest.ical_ok),
        ) if not ok
    ]
    if failed:
        stale = " Showing last good data." if display is not latest else ""
        context["banner"] = {
            "kind": "error" if not latest.jira_ok else "warn",
            "text": f"Latest refresh failed for: {', '.join(failed)}.{stale}",
        }
    return context


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    try:
        selected = _parse_regions(request.query_params.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)

    context = _dashboard_context(request, selected, _now())
    return _templates(request).TemplateResponse(request, "index.html", context)


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None or ctx.secrets is None:
        return render_setup(request)
    form = await request.form()
    try:
        selected = _parse_regions(form.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)

    await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=_now())
    context = _dashboard_context(request, selected, _now())
    return _templates(request).TemplateResponse(request, "_dashboard.html", context)


@router.get("/chip/{engineer_email}/detail", response_class=HTMLResponse)
async def chip_detail(request: Request, engineer_email: str) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    if engineer_email not in config.ENGINEERS_BY_EMAIL:
        return PlainTextResponse("Unknown engineer", status_code=404)

    now = _now()
    requested = request.query_params.getlist("regions")
    region_key = next((r for r in requested if r in config.REGIONS), None)
    if region_key is None:
        region_key = config.primary_region_for(engineer_email) or config.REGION_KEYS[0]

    db = ctx.db
    latest = db.latest_fetch()
    if latest is None:
        return PlainTextResponse("No data yet", status_code=404)

    strict_mode = db.get_ui_state("bvg_strict_mode", "off") == "on"
    data = presenters.load_fetch_data(db, latest.fetched_at, latest.id)
    panel = presenters.build_panel(
        db, engineer_email, data, now, region_key=region_key, strict_mode=strict_mode
    )
    return _templates(request).TemplateResponse(
        request, "_detail_panel.html", {"panel": panel}
    )


# --- US2: schedule modal + role/strict mutations ---------------------------


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_modal(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    db = ctx.db
    weekly = db.get_weekly_schedule()
    defaults: dict[tuple[str, str], str] = {}
    for eng in config.ROSTER:
        for wd in WEEKDAYS:
            default = "OFF" if wd == "WEEKEND" else "GEN"
            defaults[(eng.email, wd)] = weekly.get((eng.email, wd), default)
    overrides = db.get_active_overrides(_now())
    return _templates(request).TemplateResponse(
        request,
        "_schedule_modal.html",
        {
            "engineers": config.ROSTER,
            "weekdays": WEEKDAYS,
            "roles": [r.value for r in Role],
            "defaults": defaults,
            "overrides": overrides,
        },
    )


@router.post("/schedule/weekly", response_class=HTMLResponse)
async def schedule_weekly(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        schedule.set_weekly_role(
            ctx.db, form["engineer_email"], form["weekday"], form["role"], _now()
        )
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(f"Invalid schedule update: {exc}", status_code=400)
    return PlainTextResponse("ok")


@router.post("/schedule/override", response_class=HTMLResponse)
async def schedule_override(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    role = form.get("role", "")
    if not role:  # blank selection — no-op
        return PlainTextResponse("ok")
    try:
        schedule.set_today_override(ctx.db, form["engineer_email"], role, _now())
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(f"Invalid override: {exc}", status_code=400)
    return PlainTextResponse("ok")


@router.post("/toggle/strict", response_class=HTMLResponse)
async def toggle_strict(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        selected = _parse_regions(form.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)

    now = _now()
    # The control is only rendered when a BVG engineer exists today (FR-010).
    if not presenters.any_bvg_today(ctx.db, selected, now):
        return PlainTextResponse("No BVG engineer today", status_code=404)

    schedule.set_strict_mode(ctx.db, form.get("value") == "on", now)
    context = _dashboard_context(request, selected, now)
    return _templates(request).TemplateResponse(request, "_dashboard.html", context)
