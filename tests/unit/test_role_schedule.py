"""US2 unit tests: override expiry, weekly fallback (T030)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from standup_dashboard.services import schedule
from standup_dashboard.storage.db import Database

# An AMER engineer (America/Mexico_City, UTC-6 in June).
EMAIL = "alexandre.gomes@canonical.com"


@pytest.fixture
def db(db_dsn):
    database = Database(db_dsn)
    yield database
    database.close()


def utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_override_expires_at_region_local_midnight(db):
    # 2026-06-11 12:00 UTC ≈ 06:00 in Mexico City → next local midnight is
    # 2026-06-12 00:00 local = 2026-06-12 06:00 UTC.
    now = utc(2026, 6, 11, 12)
    schedule.set_today_override(db, EMAIL, "OFF", now)

    assert db.get_active_overrides(now) == {EMAIL: "OFF"}
    # Just before local midnight — still active.
    assert db.get_active_overrides(utc(2026, 6, 12, 5)) == {EMAIL: "OFF"}
    # After local midnight — expired.
    assert db.get_active_overrides(utc(2026, 6, 12, 6, 1)) == {}


def test_weekly_default_persists_latest_wins(db):
    now = utc(2026, 6, 11)
    schedule.set_weekly_role(db, EMAIL, "MON", "GEN", now)
    schedule.set_weekly_role(db, EMAIL, "MON", "BVG", utc(2026, 6, 11, 13))
    assert db.get_weekly_schedule()[(EMAIL, "MON")] == "BVG"


def test_set_weekly_role_validates(db):
    now = utc(2026, 6, 11)
    with pytest.raises(ValueError):
        schedule.set_weekly_role(db, EMAIL, "FUNDAY", "GEN", now)
    with pytest.raises(ValueError):
        schedule.set_weekly_role(db, EMAIL, "MON", "NOPE", now)


def test_calendar_day_off_beats_weekly_but_not_manual_override(db):
    """A calendar day-off (``pto_today``) resolves to OFF over the weekly schedule,
    but a manual today-override still wins (#cal-off)."""
    from standup_dashboard.domain.models import Role
    from standup_dashboard.web.presenters import resolve_roles
    now = utc(2026, 6, 11, 12)          # Thursday (06:00 in Mexico City)
    tz = "America/Mexico_City"
    schedule.set_weekly_role(db, EMAIL, "THU", "PVG", now)

    assert resolve_roles(db, [EMAIL], tz, now)[EMAIL] is Role.PVG          # schedule
    # Calendar marks today off → OFF, overriding the weekly PVG.
    assert resolve_roles(db, [EMAIL], tz, now, {EMAIL})[EMAIL] is Role.OFF
    # A manual today-override beats the calendar day-off.
    schedule.set_today_override(db, EMAIL, "BVG", now)
    assert resolve_roles(db, [EMAIL], tz, now, {EMAIL})[EMAIL] is Role.BVG


# --- On-call handover stamping incl. unassigned counterpart (#handover) ------

APAC_PVG = "paul.collins@canonical.com"
AMER_PVG = "colin.misare@canonical.com"


def _pvg_chip(group):
    from standup_dashboard.domain.models import Role
    return next(c for c in group.chips if c.role == Role.PVG)


def test_handover_stamps_region_and_marks_unassigned_counterpart(db):
    """APAC→EMEA→AMER rotation: a PVG sees its counterpart *region* both ways, and
    the name is blank (UI shows 'unassigned') when that region has no PVG set.

    EMEA gets no PVG here, so APAC's hand-over-to and AMER's receive-from both
    name the EMEA region with no holder — the exact case the user hit."""
    from standup_dashboard.web.presenters import DashboardData, build_chip_groups

    # Wed 2026-06-17 12:00 UTC is a weekday in all three regions (Sydney 22:00,
    # Paris 14:00, Mexico 06:00), so the weekly schedule — not the weekend rule —
    # drives roles. WED is the slot everywhere at this instant.
    now = utc(2026, 6, 17, 12)
    schedule.set_weekly_role(db, APAC_PVG, "WED", "PVG", now)
    schedule.set_weekly_role(db, AMER_PVG, "WED", "PVG", now)
    # EMEA: nobody set → all default GEN → no EMEA PVG holder.

    data = DashboardData(fetched_at=now)
    groups, _ = build_chip_groups(db, data, ["APAC", "EMEA", "AMER"], now)
    by_key = {g.key: g for g in groups}

    apac = _pvg_chip(by_key["APAC"])
    assert (apac.handover_from_region, apac.handover_from) == ("AMER", "Colin Misare")
    assert (apac.handover_to_region, apac.handover_to) == ("EMEA", "")  # unassigned

    amer = _pvg_chip(by_key["AMER"])
    assert (amer.handover_to_region, amer.handover_to) == ("APAC", "Paul Collins")
    assert (amer.handover_from_region, amer.handover_from) == ("EMEA", "")  # unassigned


# --- Weekend view: next-week preview + on-call PVG hand-over to APAC (#weekend-preview)

AMER_ONCALL = "colin.misare@canonical.com"
AMER_OTHER = "alexandre.gomes@canonical.com"
APAC_MON_PVG = "james.simpson@canonical.com"


def test_weekend_chips_preview_next_week_and_oncall_hands_over_to_apac(db):
    """On a region-local weekend the on-call shows PVG (not OFF) and hands the duty
    over to APAC's Monday PVG; everyone else previews their Monday role, not OFF."""
    from datetime import date

    from standup_dashboard.domain.models import Role, WeekendOnCall
    from standup_dashboard.web.presenters import DashboardData, build_chip_groups

    # Sun 2026-06-21 18:00 in Mexico City == Mon 2026-06-22 00:00 UTC == Mon 10:00 in
    # Sydney: AMER is on its weekend while APAC has already rolled into Monday.
    now = utc(2026, 6, 22, 0)
    schedule.set_weekly_role(db, APAC_MON_PVG, "MON", "PVG", now)
    schedule.set_weekly_role(db, AMER_OTHER, "MON", "BVG", now)

    data = DashboardData(
        fetched_at=now,
        weekend_oncall=[WeekendOnCall(AMER_ONCALL, date(2026, 6, 20), date(2026, 6, 21))],
    )
    groups, _ = build_chip_groups(db, data, ["APAC", "AMER"], now)
    amer = {c.email: c for c in next(g for g in groups if g.key == "AMER").chips}

    # The on-call covers the weekend as PVG and hands over to APAC's Monday PVG.
    oncall = amer[AMER_ONCALL]
    assert oncall.role is Role.PVG
    assert (oncall.handover_to_region, oncall.handover_to) == ("APAC", "James Simpson")

    # A non-on-call AMER member previews next week's Monday role instead of OFF,
    # and a preview chip carries no spurious hand-over line.
    other = amer[AMER_OTHER]
    assert other.role is Role.BVG
    assert not other.handover_to_region


def test_handover_resolves_when_apac_is_still_in_its_own_weekend(db):
    """Earlier in the weekend (Sat in AMER) APAC is itself on Sunday, so it reads its
    WEEKEND slot, not Monday. The on-call's '→ APAC' line must still resolve to APAC's
    incoming Monday PVG via the display-role preview, not show 'unassigned'."""
    from datetime import date

    from standup_dashboard.domain.models import Role, WeekendOnCall
    from standup_dashboard.web.presenters import DashboardData, build_chip_groups

    # Sat 2026-06-20 18:00 in Mexico City == 2026-06-21 00:00 UTC == Sun 10:00 in
    # Sydney: AMER, EMEA and APAC are *all* on their local weekend.
    now = utc(2026, 6, 21, 0)
    schedule.set_weekly_role(db, APAC_MON_PVG, "MON", "PVG", now)

    data = DashboardData(
        fetched_at=now,
        weekend_oncall=[WeekendOnCall(AMER_ONCALL, date(2026, 6, 20), date(2026, 6, 21))],
    )
    groups, _ = build_chip_groups(db, data, ["APAC", "AMER"], now)
    amer = {c.email: c for c in next(g for g in groups if g.key == "AMER").chips}

    oncall = amer[AMER_ONCALL]
    assert oncall.role is Role.PVG
    assert (oncall.handover_to_region, oncall.handover_to) == ("APAC", "James Simpson")
