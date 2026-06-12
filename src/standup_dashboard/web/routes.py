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

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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
        "counts_rows": [],
        "banner": None,
        "ready": True,            # the roster always renders, fetch or not
        "refreshing": ctx.refresh.running,
        "chip_groups": [],
        "global_chips": [],
        "last_fetch_label": "No fetch yet",
    }

    latest = db.latest_fetch()
    # Last-good fallback (US6/FR-028): if the latest fetch's primary source
    # (Jira) failed, render the most recent fetch where it succeeded.
    display = latest
    if latest is not None and not latest.jira_ok:
        good = db.latest_good_fetch()
        if good is not None:
            display = good

    if display is not None:
        data = presenters.load_fetch_data(db, display.fetched_at, display.id)
        context["last_fetch_label"] = _fmt_fetch(display, selected_regions, now)
    else:
        # No fetch yet — still show the team (zero activity).
        data = presenters.DashboardData(fetched_at=now)
        context["last_fetch_label"] = "No fetch yet — showing roster"

    chip_groups, global_chips = presenters.build_chip_groups(db, data, selected_regions, now)
    context.update(
        chip_groups=chip_groups,
        global_chips=global_chips,
        counts_rows=presenters.build_counts(data, selected_regions, now),
    )

    if latest is not None:
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


async def _run_refresh_bg(ctx) -> None:
    """Run a fetch in the background; the UI polls /refresh/status for completion."""
    try:
        await run_fetch(ctx.db, ctx.snapshots, ctx.secrets, now=_now())
    except Exception:  # noqa: BLE001
        logger.exception("Background refresh failed")
        ctx.refresh.error = "Refresh failed — see server logs."
    finally:
        ctx.refresh.running = False


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request, background: BackgroundTasks) -> Response:
    ctx = _ctx(request)
    if ctx.setup_error is not None or ctx.secrets is None:
        return render_setup(request)
    form = await request.form()
    try:
        selected = _parse_regions(form.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)

    # Fire-and-forget: the fetch runs server-side, the UI keeps reading the DB.
    if not ctx.refresh.running:
        ctx.refresh.running = True
        ctx.refresh.error = None
        background.add_task(_run_refresh_bg, ctx)

    context = _dashboard_context(request, selected, _now())
    context["refreshing"] = True
    return _templates(request).TemplateResponse(
        request, "_dashboard.html", context, background=background
    )


@router.get("/refresh/status")
async def refresh_status(request: Request) -> Response:
    """Poll target: 204 while a refresh runs, then ask HTMX to reload the page."""
    ctx = _ctx(request)
    if ctx.refresh.running:
        return Response(status_code=204)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


def _resolve_region(engineer_email: str, requested: list[str]) -> str:
    region_key = next((r for r in requested if r in config.REGIONS), None)
    return region_key or config.primary_region_for(engineer_email) or config.REGION_KEYS[0]


def _render_panel(request: Request, engineer_email: str, region_key: str) -> HTMLResponse:
    ctx = _ctx(request)
    db = ctx.db
    now = _now()
    latest = db.latest_fetch()
    data = (
        presenters.load_fetch_data(db, latest.fetched_at, latest.id)
        if latest is not None
        else presenters.DashboardData(fetched_at=now)
    )
    panel = presenters.build_panel(
        db, engineer_email, data, now,
        region_key=region_key, strict_mode=schedule.get_strict_mode(db),
    )
    return _templates(request).TemplateResponse(
        request, "_detail_panel.html",
        {"panel": panel, "region_key": region_key, "roles": [r.value for r in Role]},
    )


@router.get("/chip/{engineer_email}/detail", response_class=HTMLResponse)
async def chip_detail(request: Request, engineer_email: str) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    if engineer_email not in config.ENGINEERS_BY_EMAIL:
        return PlainTextResponse("Unknown engineer", status_code=404)
    region_key = _resolve_region(engineer_email, request.query_params.getlist("regions"))
    return _render_panel(request, engineer_email, region_key)


@router.post("/chip/{engineer_email}/role", response_class=HTMLResponse)
async def chip_role(request: Request, engineer_email: str) -> HTMLResponse:
    """Set a today-only role override from the panel and re-render it recolored."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    if engineer_email not in config.ENGINEERS_BY_EMAIL:
        return PlainTextResponse("Unknown engineer", status_code=404)
    form = await request.form()
    region_key = _resolve_region(engineer_email, form.getlist("regions"))
    try:
        schedule.set_today_override(ctx.db, engineer_email, form["role"], _now())
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(f"Invalid role: {exc}", status_code=400)
    return _render_panel(request, engineer_email, region_key)


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
