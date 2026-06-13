"""Follow-the-sun ticket→region attribution by creation time (UTC windows)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from standup_dashboard import config


def at(h, m=0, tz=UTC):
    return datetime(2026, 6, 12, h, m, tzinfo=tz)


@pytest.mark.parametrize(
    "hour, region",
    [
        (7, "EMEA"), (10, "EMEA"), (14, "EMEA"),     # 07:00–15:00 UTC
        (15, "AMER"), (19, "AMER"), (22, "AMER"),    # 15:00–23:00 UTC
        (23, "APAC"), (0, "APAC"), (3, "APAC"), (6, "APAC"),  # 23:00–07:00 UTC
    ],
)
def test_creation_region_by_utc_hour(hour, region):
    assert config.region_for_creation(at(hour)) == region


def test_boundaries_are_half_open_at_the_start():
    # Exactly on a boundary belongs to the starting window.
    assert config.region_for_creation(at(7, 0)) == "EMEA"     # 07:00 → EMEA
    assert config.region_for_creation(at(6, 59)) == "APAC"    # 06:59 → APAC
    assert config.region_for_creation(at(15, 0)) == "AMER"    # 15:00 → AMER
    assert config.region_for_creation(at(23, 0)) == "APAC"    # 23:00 → APAC


def test_windows_tile_the_full_day_with_no_gaps_or_overlaps():
    # Every hour of the day maps to exactly one region.
    seen = {config.region_for_creation(at(h)) for h in range(24)}
    assert seen == {"AMER", "APAC", "EMEA"}
    assert all(config.region_for_creation(at(h)) is not None for h in range(24))


def test_local_time_is_converted_to_utc_first():
    # 12:00 in Sydney (UTC+10 in June) == 02:00 UTC → APAC window.
    syd = at(12, tz=ZoneInfo("Australia/Sydney"))
    assert config.region_for_creation(syd) == "APAC"


def test_naive_datetime_treated_as_utc():
    assert config.region_for_creation(datetime(2026, 6, 12, 10)) == "EMEA"
