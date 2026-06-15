"""Alert health-band coloring (green/yellow/red) for the counts tables.

Volume columns (Ack/Total) use a region-scaled cap; the resolve rate and the
MTTR/MTTA means are rates, so they share fixed thresholds. See domain/coloring.py.
"""

from __future__ import annotations

from standup_dashboard.domain.coloring import (
    closed_vs_new_level,
    closed_vs_new_total_level,
    count_level,
    mtta_level,
    mttr_level,
    pr_mp_review_level,
    resolve_rate_level,
)
from standup_dashboard.domain.models import Color


def test_closed_vs_new_level():
    assert closed_vs_new_level(0, 0) is Color.YELLOW  # equal (incl. 0=0) → yellow
    assert closed_vs_new_level(3, 2) is Color.GREEN   # closed more than new
    assert closed_vs_new_level(2, 2) is Color.YELLOW  # equal
    assert closed_vs_new_level(1, 2) is Color.RED     # closed fewer


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
