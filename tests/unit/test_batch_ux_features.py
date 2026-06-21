"""Batch UX features (#ribbon #focus-toggle #off-distractor #handover).

Covers the priority ribbon, the ps5-blocker exemption in the Highest-focus
toggle, the OFF-day distractor fix, and the on-call handover rotation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import Pulse, Role, Ticket
from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import (
    DashboardData,
    _handover_name,
    build_panel,
)

E = "alexandre.gomes@canonical.com"  # AMER
TZ = "America/Mexico_City"
NOW = datetime(2026, 6, 12, 18, tzinfo=UTC)  # Friday, inside anchored pulse 12
SPRINT = 1


def _data(*tickets: Ticket) -> DashboardData:
    return DashboardData(
        fetched_at=NOW,
        pulses=[Pulse("ISReq", SPRINT, "s", NOW, NOW)],
        tickets=list(tickets),
    )


def _t(key: str, *, priority="Medium", labels=None) -> Ticket:
    return Ticket(key, "ISReq", "t", "In Progress", priority,
                  labels=labels or [], assignee_email=E, sprint_id=SPRINT)


def _groups(panel) -> dict[str, list[str]]:
    return {g: [vm.key for vm in vms] for g, vms in panel.groups.items()}


# --- Priority ribbon (#ribbon) ---------------------------------------------

def test_priority_ribbon_mapping():
    mk = lambda p: Ticket("ISReq-1", "ISReq", "t", "In Progress", p)  # noqa: E731
    assert mk("Highest").priority_ribbon == "H2"
    assert mk("High").priority_ribbon == "H1"
    assert mk("Medium").priority_ribbon == "M"
    assert mk("Low").priority_ribbon == "L1"
    assert mk("Lowest").priority_ribbon == "L2"
    assert mk(None).priority_ribbon == ""
    assert mk("Bogus").priority_ribbon == ""


def test_panel_ticket_carries_ribbon(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    panel = build_panel(db, E, _data(_t("ISReq-1", priority="Highest")), NOW,
                        region_key="AMER")
    vm = next(vm for vms in panel.groups.values() for vm in vms if vm.key == "ISReq-1")
    assert (vm.ribbon, vm.priority) == ("H2", "Highest")
    db.close()


# --- Highest-focus toggle now exempts ps5-blockers (#focus-toggle) ----------

def test_ps5_blocker_exempt_from_highest_focus(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    data = _data(_t("ISReq-ps5", labels=["ps5-blocker"]), _t("ISReq-reg"))
    panel = build_panel(db, E, data, NOW, region_key="AMER", highest_focus=True)
    groups = _groups(panel)
    assert "ISReq-ps5" in groups.get("WIP", [])             # ps5 spared
    assert "ISReq-reg" in groups.get("Distractors", [])     # plain ISReq focused out
    db.close()


# --- OFF day no longer distracts the engineer's own work (#off-distractor) ---

def test_off_day_keeps_assigned_work_in_wip(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "OFF", NOW)
    panel = build_panel(db, E, _data(_t("ISReq-1")), NOW, region_key="AMER")
    groups = _groups(panel)
    assert "ISReq-1" in groups.get("WIP", [])
    assert "ISReq-1" not in groups.get("Distractors", [])
    db.close()


# --- On-call handover rotation APAC -> EMEA -> AMER -> APAC (#handover) ------

def test_handover_rotation_order():
    holders = {("APAC", Role.PVG): "Ann", ("EMEA", Role.PVG): "Bob",
               ("AMER", Role.PVG): "Cy"}
    # +1 = hands over to the next region; -1 = receives from the previous one.
    assert _handover_name(holders, "APAC", Role.PVG, +1) == "Bob"
    assert _handover_name(holders, "APAC", Role.PVG, -1) == "Cy"   # wraps to AMER
    assert _handover_name(holders, "EMEA", Role.PVG, +1) == "Cy"
    assert _handover_name(holders, "AMER", Role.PVG, +1) == "Ann"  # wraps to APAC
    # A role with no counterpart in the target region yields no name.
    assert _handover_name(holders, "APAC", Role.BVG, +1) == ""
