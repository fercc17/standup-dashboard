"""Alert health-band coloring (green/yellow/red) for the counts tables.

Volume columns (Ack/Total) use a region-scaled cap; the resolve rate and the
MTTR/MTTA means are rates, so they share fixed thresholds. See domain/coloring.py.
"""

from __future__ import annotations

from standup_dashboard.domain.coloring import (
    count_level,
    mtta_level,
    mttr_level,
    resolve_rate_level,
)
from standup_dashboard.domain.models import Color


def test_count_level_bands_single_region():
    # cap 2 (weekday, one region): 0–2 green, 3–4 yellow, 5+ red.
    assert count_level(0, 2) is Color.GREEN
    assert count_level(2, 2) is Color.GREEN
    assert count_level(3, 2) is Color.YELLOW
    assert count_level(4, 2) is Color.YELLOW
    assert count_level(5, 2) is Color.RED


def test_count_level_scales_with_cap():
    # Two regions double the cap (2×2=4): 0–4 green, 5–8 yellow, 9+ red.
    assert count_level(4, 4) is Color.GREEN
    assert count_level(8, 4) is Color.YELLOW
    assert count_level(9, 4) is Color.RED


def test_count_level_no_cap_is_neutral():
    assert count_level(100, 0) is None


def test_resolve_rate_bands():
    assert resolve_rate_level(0, 0) is None        # nothing acked → neutral
    assert resolve_rate_level(8, 10) is Color.GREEN   # 80%
    assert resolve_rate_level(10, 10) is Color.GREEN  # 100%
    assert resolve_rate_level(13, 10) is Color.GREEN  # >100% (cross-period) still green
    assert resolve_rate_level(7, 10) is Color.YELLOW  # 70%
    assert resolve_rate_level(5, 10) is Color.YELLOW  # 50% (boundary)
    assert resolve_rate_level(4, 10) is Color.RED     # 40%


def test_mttr_bands():
    assert mttr_level(None) is None
    assert mttr_level(30 * 60) is Color.GREEN          # 30m boundary
    assert mttr_level(30 * 60 + 1) is Color.YELLOW
    assert mttr_level(2 * 60 * 60) is Color.YELLOW     # 2h boundary
    assert mttr_level(2 * 60 * 60 + 1) is Color.RED


def test_mtta_bands():
    assert mtta_level(None) is None
    assert mtta_level(5 * 60) is Color.GREEN           # 5m boundary
    assert mtta_level(5 * 60 + 1) is Color.YELLOW
    assert mtta_level(15 * 60) is Color.YELLOW         # 15m boundary
    assert mtta_level(15 * 60 + 1) is Color.RED
