"""Batch UX features (#ribbon #focus-toggle #off-distractor #handover).

Covers the priority ribbon, the ps5-blocker exemption in the Highest-focus
toggle, the OFF-day distractor fix, and the on-call handover rotation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import (
    Alert,
    AlertState,
    Pulse,
    Role,
    Ticket,
)
from standup_dashboard.domain.roles import region_weekday
from standup_dashboard.storage.db import Database
from standup_dashboard.web.presenters import (
    DashboardData,
    _handover_name,
    _handover_region,
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

def _vm(panel, key):
    for vms in panel.groups.values():
        for vm in vms:
            if vm.key == key:
                return vm
    return None


def test_highest_focus_flags_offfocus_isreq_in_place(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    data = _data(_t("ISReq-ps5", labels=["ps5-blocker"]), _t("ISReq-reg"))
    panel = build_panel(db, E, data, NOW, region_key="AMER", highest_focus=True)
    # ps5-blocker is exempt — not flagged, stays in WIP.
    assert _vm(panel, "ISReq-ps5").flagged is False
    assert "ISReq-ps5" in _groups(panel).get("WIP", [])
    # The plain ISReq is flagged in place (the toggle only flags, it doesn't move
    # it out of view — its group is whatever the role rules say, here Distractors).
    assert _vm(panel, "ISReq-reg").flagged is True


def test_highest_focus_off_flags_nothing(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "GEN", NOW)
    panel = build_panel(db, E, _data(_t("ISReq-reg")), NOW, region_key="AMER",
                        highest_focus=False)
    assert _vm(panel, "ISReq-reg").flagged is False
    db.close()


# --- OFF day no longer distracts the engineer's own work (#off-distractor) ---

def _isdb(key: str) -> Ticket:
    return Ticket(key, "ISDB", "t", "In Progress", "Medium",
                  assignee_email=E, sprint_id=SPRINT)


def test_off_day_keeps_assigned_wip_on_task_both_projects(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, region_weekday(NOW, TZ), "OFF", NOW)  # OFF today
    panel = build_panel(db, E, _data(_t("ISReq-1"), _isdb("ISDB-9")), NOW,
                        region_key="AMER")
    wip = {vm.key: vm for vm in panel.groups.get("WIP", [])}
    # Both ISReq and ISDB assigned WIP stay under WIP, coloured on-task (not red).
    assert set(wip) == {"ISReq-1", "ISDB-9"}
    assert wip["ISReq-1"].color.value == "green"
    assert wip["ISDB-9"].color.value == "green"
    assert "ISReq-1" not in _groups(panel).get("Distractors", [])
    db.close()


def _find_alert(panel, needle):
    """(group, color) of the alert row whose title contains ``needle``."""
    for grp, vms in panel.groups.items():
        for vm in vms:
            if vm.key == "⚠" and needle in vm.title:
                return grp, vm.color.value
    return None, None


def test_off_day_alert_classified_by_coverage_day(db_dsn):
    db = Database(db_dsn)
    db.set_weekly_role(E, "FRI", "OFF", NOW)   # today (Fri 2026-06-12) is OFF
    db.set_weekly_role(E, "TUE", "PVG", NOW)   # was the primary on-call Tuesday
    db.set_weekly_role(E, "MON", "OFF", NOW)   # genuinely off Monday
    tue = datetime(2026, 6, 9, 10, tzinfo=UTC)
    mon = datetime(2026, 6, 8, 10, tzinfo=UTC)
    data = DashboardData(
        fetched_at=NOW, pulses=[Pulse("ISReq", SPRINT, "s", NOW, NOW)],
        alerts=[Alert("INCA", E, AlertState.RESOLVED, tue, title="Tue oncall", number=1),
                Alert("INCB", E, AlertState.RESOLVED, mon, title="Mon offday", number=2)],
    )
    panel = build_panel(db, E, data, NOW, region_key="AMER")
    # Covered while PVG → real on-call work (resolved → Success, green).
    assert _find_alert(panel, "Tue oncall") == ("Success", "green")
    # Covered on a genuine off day → still a distraction.
    assert _find_alert(panel, "Mon offday")[0] == "Distractors"
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


def test_handover_region_rotation():
    # The counterpart region is named regardless of whether it has a holder, so
    # the rotation stays legible (and an empty region reads as "unassigned").
    assert _handover_region("APAC", +1) == "EMEA"   # hands over to
    assert _handover_region("APAC", -1) == "AMER"   # receives from (wraps)
    assert _handover_region("EMEA", +1) == "AMER"
    assert _handover_region("AMER", +1) == "APAC"   # wraps
    assert _handover_region("Management", +1) == ""  # off-cycle
