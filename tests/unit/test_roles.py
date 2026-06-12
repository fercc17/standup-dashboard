"""Effective-role resolution tests (FR-009) — T015."""

from __future__ import annotations

from datetime import UTC, datetime

from standup_dashboard.domain.models import Role
from standup_dashboard.domain.roles import (
    DEFAULT_WEEKDAY_ROLE,
    DEFAULT_WEEKEND_ROLE,
    effective_role,
    region_weekday,
)

AMER = "America/Mexico_City"
APAC = "Australia/Sydney"

EMAIL = "eng@example.com"


def utc(y, m, d, h=12):
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def test_region_weekday_amer():
    # 2026-06-11 is a Thursday; 12:00 UTC is still Thursday in Mexico City.
    assert region_weekday(utc(2026, 6, 11), AMER) == "THU"


def test_region_weekday_rolls_over_per_timezone():
    # 2026-06-11 23:00 UTC: Mexico City still Thu 17:00; Sydney already Fri 09:00.
    t = utc(2026, 6, 11, 23)
    assert region_weekday(t, AMER) == "THU"
    assert region_weekday(t, APAC) == "FRI"


def test_override_wins():
    role = effective_role(
        EMAIL, AMER, utc(2026, 6, 11),
        weekly_schedule={(EMAIL, "THU"): "GEN"},
        active_overrides={EMAIL: "OFF"},
    )
    assert role is Role.OFF


def test_weekly_default_when_no_override():
    role = effective_role(
        EMAIL, AMER, utc(2026, 6, 11),
        weekly_schedule={(EMAIL, "THU"): "Project"},
        active_overrides={},
    )
    assert role is Role.PROJECT


def test_weekday_default_when_unset():
    role = effective_role(EMAIL, AMER, utc(2026, 6, 11), {}, {})
    assert role is DEFAULT_WEEKDAY_ROLE


def test_weekend_default_off():
    # 2026-06-13 is a Saturday.
    role = effective_role(EMAIL, AMER, utc(2026, 6, 13), {}, {})
    assert role is DEFAULT_WEEKEND_ROLE


def test_weekend_slot_uses_weekend_schedule():
    role = effective_role(
        EMAIL, AMER, utc(2026, 6, 13),
        weekly_schedule={(EMAIL, "WEEKEND"): "BVG"},
        active_overrides={},
    )
    assert role is Role.BVG
