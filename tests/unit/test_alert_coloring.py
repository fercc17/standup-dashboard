"""Alert health-band coloring (green/yellow/red) for the counts tables.

Volume columns (Ack/Total) use a region-scaled cap; the resolve rate and the
MTTR/MTTA means are rates, so they share fixed thresholds. See domain/coloring.py.
"""

from __future__ import annotations

from standup_dashboard.domain.coloring import (
    ALERT_MTTA_GREEN_S,
    ALERT_MTTR_GREEN_S,
    closed_vs_new_level,
    closed_vs_new_total_level,
    count_level,
    cycle_color,
    cycle_trend_level,
    intake_level,
    mtta_level,
    mtta_trend_level,
    mttr_level,
    mttr_trend_level,
    pr_mp_review_level,
    resolve_rate_level,
)
from standup_dashboard.domain.models import Color


def test_closed_vs_new_level():
    assert closed_vs_new_level(0, 0) is Color.YELLOW  # equal (incl. 0=0) → yellow
    assert closed_vs_new_level(3, 2) is Color.GREEN   # closed more than new
    assert closed_vs_new_level(2, 2) is Color.YELLOW  # equal
    assert closed_vs_new_level(1, 2) is Color.RED     # closed fewer


def test_cycle_trend_level():
    assert cycle_trend_level(5.0, None) is None        # no baseline
    assert cycle_trend_level(None, 5.0) is None        # no current data
    assert cycle_trend_level(4.0, 5.0) is Color.GREEN  # faster (>10% lower)
    assert cycle_trend_level(6.0, 5.0) is Color.RED     # slower (>10% higher)
    assert cycle_trend_level(5.2, 5.0) is Color.YELLOW  # within ±10%
    assert cycle_trend_level(4.6, 5.0) is Color.YELLOW  # -8% within tolerance


def test_intake_level():
    # Sub-columns (no floor): pure trend vs the previous pulse, fewer = green.
    assert intake_level(3, None) is None              # no baseline
    assert intake_level(0, 0) is None                 # sustained zero / no data
    assert intake_level(2, 6) is Color.GREEN          # intake fell ≥ margin
    assert intake_level(9, 6) is Color.RED            # intake rose ≥ margin
    assert intake_level(7, 6) is Color.YELLOW         # +1 within the ±2 noise band
    assert intake_level(0, 5) is Color.GREEN          # dropped to zero (good)
    # New Total (with healthy floor = historical average): at/below floor = green.
    assert intake_level(4, 99, floor=5.0) is Color.GREEN   # below floor beats trend
    assert intake_level(5, 99, floor=5.0) is Color.GREEN   # at floor
    assert intake_level(8, 6, floor=5.0) is Color.RED      # above floor, rising
    assert intake_level(8, 12, floor=5.0) is Color.GREEN   # above floor but falling
    assert intake_level(0, 0, floor=5.0) is None           # zero isn't "healthy load"


def test_cycle_color():
    # No cycle data → neutral, even when closed > new.
    assert cycle_color(None, 5.0, 10, 1) is None
    # closed > new → green regardless of trend (even slower, even no baseline).
    assert cycle_color(6.0, 5.0, 10, 1) is Color.GREEN   # slower but out-closed intake
    assert cycle_color(9.0, None, 10, 1) is Color.GREEN  # no baseline, out-closed intake
    # closed <= new → fall back to trend vs the previous pulse.
    assert cycle_color(6.0, 5.0, 1, 10) is Color.RED     # slower
    assert cycle_color(4.0, 5.0, 1, 10) is Color.GREEN   # faster
    assert cycle_color(5.2, 5.0, 1, 10) is Color.YELLOW  # within ±10%
    assert cycle_color(5.0, 5.0, 5, 5) is Color.YELLOW   # equal isn't "more closed"
    assert cycle_color(5.0, None, 1, 10) is None         # no baseline, no override


def test_closed_vs_new_total_level():
    assert closed_vs_new_total_level(0, 0, 1) is None
    # 1 region → ±2 margin.
    assert closed_vs_new_total_level(12, 10, 1) is Color.GREEN    # +2
    assert closed_vs_new_total_level(11, 10, 1) is Color.YELLOW   # +1 within
    assert closed_vs_new_total_level(10, 10, 1) is Color.YELLOW   # equal
    assert closed_vs_new_total_level(8, 10, 1) is Color.RED       # -2
    # 2 regions → ±4 margin.
    assert closed_vs_new_total_level(13, 10, 2) is Color.YELLOW   # +3 < 4
    assert closed_vs_new_total_level(14, 10, 2) is Color.GREEN    # +4
    assert closed_vs_new_total_level(6, 10, 2) is Color.RED       # -4


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


def test_mtta_trend_level():
    # No current data → neutral, whatever the baseline.
    assert mtta_trend_level(None, 600.0) is None
    # At/below the 5m floor is always green and ignores the trend.
    assert mtta_trend_level(ALERT_MTTA_GREEN_S, None) is Color.GREEN
    assert mtta_trend_level(120.0, 9999.0) is Color.GREEN
    # Above the floor with no previous-pulse baseline → neutral.
    assert mtta_trend_level(900.0, None) is None
    # Above the floor → colour vs the previous pulse (±10% band).
    assert mtta_trend_level(600.0, 900.0) is Color.GREEN   # faster (>10% lower)
    assert mtta_trend_level(1200.0, 900.0) is Color.RED    # slower (>10% higher)
    assert mtta_trend_level(940.0, 900.0) is Color.YELLOW  # within +10%
    assert mtta_trend_level(820.0, 900.0) is Color.YELLOW  # -9% within tolerance


def test_mttr_trend_level():
    # No current data → neutral, whatever the baseline.
    assert mttr_trend_level(None, 3600.0) is None
    # At/below the 30m floor is always green and ignores the trend.
    assert mttr_trend_level(ALERT_MTTR_GREEN_S, None) is Color.GREEN
    assert mttr_trend_level(600.0, 99999.0) is Color.GREEN
    # Above the floor with no previous-pulse baseline → neutral.
    assert mttr_trend_level(3600.0, None) is None
    # Above the floor → colour vs the previous pulse (±10% band).
    assert mttr_trend_level(3600.0, 7200.0) is Color.GREEN   # faster (>10% lower)
    assert mttr_trend_level(9000.0, 7200.0) is Color.RED     # slower (>10% higher)
    assert mttr_trend_level(7400.0, 7200.0) is Color.YELLOW  # within +10%


def test_pr_mp_review_keep_up_bands():
    # review (New PR/MP) vs closed (Closed PR/MP): deficit = review - closed.
    assert pr_mp_review_level(0, 0) is None             # no activity → neutral
    assert pr_mp_review_level(5, 5) is Color.GREEN      # matched
    assert pr_mp_review_level(5, 6) is Color.GREEN      # closed more (another region left one)
    assert pr_mp_review_level(5, 7) is Color.GREEN      # well ahead
    assert pr_mp_review_level(0, 3) is Color.GREEN      # closed backlog, none came in
    assert pr_mp_review_level(5, 4) is Color.YELLOW     # exactly one behind (ok)
    assert pr_mp_review_level(3, 2) is Color.YELLOW
    assert pr_mp_review_level(5, 3) is Color.RED        # two behind
    assert pr_mp_review_level(5, 0) is Color.RED
