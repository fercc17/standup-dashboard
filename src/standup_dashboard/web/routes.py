"""FastAPI routes (contracts/internal-web.md).

Serves the dashboard shell + setup page (Phase 2) and the US1 surface: the
full page, manual refresh, and additive engineer detail panels. Later phases
add schedule/toggle routes and the counts table. No route mutates Jira or
PagerDuty (FR-027).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from .. import config
from ..domain.models import WEEKDAY_SLOTS, FetchSnapshot, Role
from ..services import aging, offenders, roster, schedule
from ..services.fetch import run_fetch, run_person_fetch
from ..services.pulse import current_pulse
from . import presenters

logger = logging.getLogger("standup_dashboard.web")

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.ctx


def _templates(request: Request):
    return request.app.state.templates


def _now() -> datetime:
    return datetime.now(UTC)


# Explicit "no region selected" marker (#152): distinguishes a deliberate
# deselect-all (zero regions) from a first visit (no param → default region).
NO_REGION = "none"


def _parse_regions(values: list[str]) -> list[str]:
    """Validate + dedupe requested regions. No param → default to the first
    region; the explicit ``none`` marker → an empty (region-less) selection."""
    if NO_REGION in values:
        return []
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
    """Toggle links for the region buttons (multi-select, FR-002/005). Turning the
    last region off carries the explicit ``none`` marker so it stays deselected."""
    links: list[dict] = []
    for r in config.REGION_KEYS:
        new = [x for x in selected if x != r] if r in selected else [*selected, r]
        query = "&".join(f"regions={x}" for x in new) or f"regions={NO_REGION}"
        links.append({"key": r, "href": f"/?{query}", "active": r in selected})
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
    axis_tz = config.REGIONS[selected_regions[0]].timezone if selected_regions else "UTC"
    pulse_number, pulse_start, _ = current_pulse(now.astimezone(ZoneInfo(axis_tz)).date())
    # Canonical pulse span is two work-weeks: Monday (wk1) → Friday (wk2). The
    # 14-day sprint's trailing weekend is non-working, so the label ends on the
    # Friday (start + 11 days), per "pulses start Monday, end Friday" (#142).
    pulse_range = f"{pulse_start:%a %d %b} – {pulse_start + timedelta(days=11):%a %d %b}"
    context: dict = {
        "regions": config.REGION_KEYS,
        "selected_regions": selected_regions,
        "pulse_number": pulse_number,
        "pulse_range": pulse_range,
        "region_links": _region_links(selected_regions),
        "highest_focus": schedule.get_highest_focus(db),
        "show_management": schedule.get_show_management(db),
        "oncall_name": None,
        "weekend_recap": None,
        "open_summary": None,
        "counts_rows": [],
        "pulse_history": [],
        "banner": None,
        "ready": True,            # the roster always renders, fetch or not
        "refreshing": ctx.refresh.running,
        "chip_groups": [],
        "management_chips": [],
        "last_fetch_label": "No fetch yet",
    }

    latest = db.latest_fetch()
    # Accumulate the pulse's fetch layers (#88): merging gives the current state
    # without dropping earlier data and transparently falls back over a failed
    # latest fetch (US6/FR-028).
    data = presenters.load_merged_data(db, now)
    if latest is not None:
        context["last_fetch_label"] = _fmt_fetch(latest, selected_regions, now)
    else:
        context["last_fetch_label"] = "No fetch yet — showing roster"

    chip_groups, management_chips = presenters.build_chip_groups(db, data, selected_regions, now)
    # Header shows the UPCOMING weekend's on-call; the just-passed one is named in
    # the recap line below it.
    next_email = data.next_oncall_email
    next_eng = config.ENGINEERS_BY_EMAIL.get(next_email) if next_email else None
    counts_full = presenters.build_counts(data, selected_regions, now)
    context.update(
        chip_groups=chip_groups,
        management_chips=management_chips,
        # The previous-pulse row moves to its own growing history table (#80).
        counts_rows=[r for r in counts_full if not r.is_previous],
        pulse_history=presenters.build_pulse_history(db, data, selected_regions, now),
        oncall_name=(next_eng.name if next_eng else next_email),
        weekend_recap=presenters.build_weekend_recap(db, data, now),
        open_summary=presenters.build_open_summary(data),
    )

    if latest is not None:
        # Per-source: each source has its own schedule, so check the latest fetch that
        # *attempted* it — False = failed, None = not yet attempted (#per-source-schedule).
        jira_failed = db.latest_source_ok("jira_ok") is False
        failed = [
            name for name, col in (
                ("Jira", "jira_ok"),
                ("PagerDuty", "pagerduty_ok"),
                ("on-call iCal", "ical_ok"),
            ) if db.latest_source_ok(col) is False
        ]
        if failed:
            stale = " Showing accumulated data." if jira_failed else ""
            context["banner"] = {
                "kind": "error" if jira_failed else "warn",
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


async def _run_refresh_bg(ctx, window_days: int | None = None) -> None:
    """Run a fetch in the background; the UI polls /refresh/status for completion.

    ``window_days`` forces a full re-fetch over that many days (default is the
    incremental window) — used to backfill a metric over the whole pulse."""
    try:
        await run_fetch(ctx.db, ctx.secrets, now=_now(), window_days=window_days)
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

    # Optional full re-fetch (?window_days=N) to backfill a metric over the pulse.
    try:
        wd = request.query_params.get("window_days")
        window_days = int(wd) if wd else None
    except ValueError:
        window_days = None

    # Fire-and-forget: the fetch runs server-side, the UI keeps reading the DB.
    if not ctx.refresh.running:
        ctx.refresh.running = True
        ctx.refresh.error = None
        background.add_task(_run_refresh_bg, ctx, window_days)

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
    data = presenters.load_merged_data(db, now)
    panel = presenters.build_panel(
        db, engineer_email, data, now,
        region_key=region_key, highest_focus=schedule.get_highest_focus(db),
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


@router.post("/chip/{engineer_email}/refresh", response_class=HTMLResponse)
async def chip_refresh(request: Request, engineer_email: str) -> HTMLResponse:
    """Refresh just this engineer's data (#person-refresh) — their Jira tickets/time,
    calendar, GitHub PRs and alerts — then re-render their panel. Fast (~a few seconds)
    because it skips the org-wide Jira scan."""
    ctx = _ctx(request)
    if ctx.setup_error is not None or ctx.secrets is None:
        return render_setup(request)
    if engineer_email not in config.ENGINEERS_BY_EMAIL:
        return PlainTextResponse("Unknown engineer", status_code=404)
    form = await request.form()
    region_key = _resolve_region(engineer_email, form.getlist("regions"))
    try:
        await run_person_fetch(ctx.db, ctx.secrets, engineer_email, now=_now())
    except Exception:  # noqa: BLE001 — re-render with whatever we have; never 500 the panel
        logger.exception("Person refresh failed for %s", engineer_email)
    return _render_panel(request, engineer_email, region_key)


# --- US2: schedule modal + role mutations ----------------------------------


def _schedule_days(now: datetime) -> list[dict]:
    """Weekdays of this week + next week for the schedule grid (#71, #day-notes).

    Roles are weekly/recurring, so their select is editable only on this-week rows;
    day notes are per *date*, editable for today and future dates."""
    today = now.date()
    monday = today - timedelta(days=now.weekday())
    days: list[dict] = []
    for wk in range(2):
        for i, slot in enumerate(WEEKDAY_SLOTS):
            d = monday + timedelta(days=wk * 7 + i)
            days.append({
                "slot": slot, "dow": d.strftime("%a"), "date": d.strftime("%b %d"),
                "iso": d.isoformat(),
                "role_editable": wk == 0,      # weekly schedule lives on this week
                "note_editable": d >= today,   # notes: today or future
                "new_week": wk == 1 and i == 0,
            })
    return days


def _render_schedule_modal(request: Request, *, summary: dict | None = None) -> HTMLResponse:
    db = _ctx(request).db
    now = _now()
    weekly = db.get_weekly_schedule()
    notes = db.get_day_notes()
    overrides = db.get_active_overrides(now)
    # Per-region sections; management is excluded (it has its own group, #72/#71).
    regions: list[dict] = []
    for key in config.REGION_KEYS:
        engs = [
            e for e in config.engineers_in_region(key)
            if not e.is_manager and not e.is_global
        ]
        if engs:
            regions.append({"key": key, "engineers": engs})
    defaults: dict[tuple[str, str], str] = {}
    for region in regions:
        for eng in region["engineers"]:
            for slot in WEEKDAY_SLOTS:
                defaults[(eng.email, slot)] = weekly.get((eng.email, slot), "GEN")
    return _templates(request).TemplateResponse(
        request,
        "_schedule_modal.html",
        {
            "regions": regions,
            "week": _schedule_days(now),
            "roles": [r.value for r in Role],
            "defaults": defaults,
            "notes": notes,
            "overrides": overrides,
            "summary": summary,
        },
    )


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_modal(request: Request) -> HTMLResponse:
    if _ctx(request).setup_error is not None:
        return render_setup(request)
    return _render_schedule_modal(request)


@router.post("/schedule/paste", response_class=HTMLResponse)
async def schedule_paste(request: Request) -> HTMLResponse:
    """Bulk-apply a tab-separated paste of the manager's spreadsheet (#71)."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    summary = schedule.apply_schedule_paste(ctx.db, form.get("paste", ""), _now())
    return _render_schedule_modal(request, summary=summary)


# --- Roster editing: add SREs + move between regions (#16) -----------------


def _render_roster_modal(request: Request, *, error: str | None = None) -> HTMLResponse:
    regions = [
        {
            "key": key,
            "engineers": [
                e for e in config.engineers_in_region(key)
                if not (e.is_manager or e.is_global)
            ],
        }
        for key in config.REGION_KEYS
    ]
    return _templates(request).TemplateResponse(
        request,
        "_roster_modal.html",
        {"regions": regions, "region_keys": config.REGION_KEYS, "error": error},
    )


@router.get("/roster", response_class=HTMLResponse)
async def roster_modal(request: Request) -> HTMLResponse:
    if _ctx(request).setup_error is not None:
        return render_setup(request)
    return _render_roster_modal(request)


@router.get("/legend", response_class=HTMLResponse)
async def legend_modal(request: Request) -> HTMLResponse:
    """Colour-rule reference (#143) — role × ticket-type colour matrix."""
    if _ctx(request).setup_error is not None:
        return render_setup(request)
    return _templates(request).TemplateResponse(
        request, "_legend_modal.html", presenters.build_color_legend()
    )


@router.get("/alerts/offenders", response_class=HTMLResponse)
async def offenders_modal(request: Request) -> HTMLResponse:
    """Repeat-offender alerts this pulse (#146) — alerts that fired 2+ times."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    # Team-wide year-history analysis (#146): alerts still firing in the last 10
    # days that have fired 10+ times this year — read from the stored incident
    # table, so no extra fetching and no region scoping.
    return _templates(request).TemplateResponse(
        request, "_offenders_modal.html",
        {"offenders": offenders.build_offenders(ctx.db, _now())},
    )


@router.get("/tickets/aging-wip", response_class=HTMLResponse)
async def aging_wip_modal(request: Request) -> HTMLResponse:
    """Tickets sitting In Progress too long, for the selected region(s) (#147)."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    try:
        selected = _parse_regions(request.query_params.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)
    members = {e for r in selected for e in config.REGIONS[r].member_emails}
    data = presenters.load_merged_data(ctx.db, _now())
    return _templates(request).TemplateResponse(
        request, "_aging_modal.html",
        {"aging": aging.build_aging_wip(data.tickets, members, _now())},
    )


@router.post("/roster/add", response_class=HTMLResponse)
async def roster_add(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        roster.add_engineer(
            ctx.db, form.get("name", ""), form.get("email", ""), form.get("region", ""), _now(),
            github_login=form.get("github_login", ""),
        )
    except ValueError as exc:
        return _render_roster_modal(request, error=str(exc))
    return _render_roster_modal(request)


@router.post("/roster/move", response_class=HTMLResponse)
async def roster_move(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        roster.move_engineer(ctx.db, form.get("email", ""), form.get("region", ""), _now())
    except ValueError as exc:
        return _render_roster_modal(request, error=str(exc))
    return _render_roster_modal(request)


@router.post("/toggle/highest")
async def toggle_highest(request: Request) -> Response:
    """Persist the 'Highest only' focus toggle (#86 follow-up)."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    schedule.set_highest_focus(ctx.db, form.get("value") == "on", _now())
    return Response(status_code=204)


@router.post("/toggle/management", response_class=HTMLResponse)
async def toggle_management(request: Request) -> Response:
    """Flip the Management-group visibility (#151) and re-render the dashboard.

    Server-persisted (``ui_state``) so it survives reloads, unlike the old
    per-browser localStorage toggle. Re-renders ``_dashboard.html`` only — no
    refetch — so the Management group appears/disappears immediately."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        selected = _parse_regions(form.getlist("regions"))
    except ValueError as exc:
        return PlainTextResponse(f"Unknown region: {exc}", status_code=400)
    schedule.set_show_management(ctx.db, not schedule.get_show_management(ctx.db), _now())
    return _templates(request).TemplateResponse(
        request, "_dashboard.html", _dashboard_context(request, selected, _now())
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


@router.post("/schedule/note", response_class=HTMLResponse)
async def schedule_note(request: Request) -> HTMLResponse:
    """Set/clear a free-text note for a specific date (#day-notes)."""
    ctx = _ctx(request)
    if ctx.setup_error is not None:
        return render_setup(request)
    form = await request.form()
    try:
        schedule.set_day_note(
            ctx.db, form["engineer_email"], form["note_date"],
            form.get("note", "").strip(), _now()
        )
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(f"Invalid note: {exc}", status_code=400)
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
