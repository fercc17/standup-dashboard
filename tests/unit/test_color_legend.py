"""Colour-rule legend matrix (#143) — derived live from domain/coloring.py."""

from __future__ import annotations

from standup_dashboard.domain.coloring import alert_color
from standup_dashboard.domain.models import Color, Role
from standup_dashboard.web.presenters import build_color_legend


def test_legend_matrix_matches_coloring_rules():
    legend = build_color_legend()
    assert legend["types"] == [
        "ISReq Highest", "ISReq [PR/MP Review]", "ISReq ps5-blocker",
        "ISReq regular", "ISDB",
    ]
    rows = {r["role"]: r["cells"] for r in legend["rows"]}

    # PVG / OFF: an assigned ticket is always red for these roles.
    assert all(c["color"] == "red" for c in rows["PVG"])
    assert all(c["color"] == "red" for c in rows["OFF"])

    # BVG keeps only Highest / [PR/MP Review] (green); the rest are red distractors.
    bvg = rows["BVG"]
    assert bvg[0] == {"color": "green", "distractor": False}   # Highest
    assert bvg[1] == {"color": "green", "distractor": False}   # PR/MP
    assert bvg[3] == {"color": "red", "distractor": True}      # regular

    # GEN: green only for ISReq Highest / ps5; others red and NOT reclassified.
    gen = rows["GEN"]
    assert gen[0]["color"] == "green"                          # Highest
    assert gen[2]["color"] == "green"                          # ps5
    assert gen[3] == {"color": "red", "distractor": False}     # regular
    assert gen[4]["color"] == "red"                            # ISDB off-task for GEN

    # Project: only ISDB is green; ISReq work is a red distractor (even Highest).
    proj = rows["Project"]
    assert proj[4] == {"color": "green", "distractor": False}  # ISDB
    assert proj[0] == {"color": "red", "distractor": True}     # ISReq Highest


def test_alert_color_is_role_based():
    # Alerts are coloured purely by the handler's role, regardless of state.
    assert alert_color(Role.PVG) is Color.GREEN
    assert alert_color(Role.BVG) is Color.YELLOW
    assert alert_color(Role.GEN) is Color.YELLOW
    assert alert_color(Role.PROJECT) is Color.RED
    assert alert_color(Role.OFF) is Color.RED


def test_legend_alerts_one_row_per_role_with_yellow():
    by_role = {r["role"]: r["color"] for r in build_color_legend()["alert_rows"]}
    assert by_role == {
        "PVG": "green", "BVG": "yellow", "GEN": "yellow", "Project": "red", "OFF": "red",
    }
    assert "yellow" in by_role.values()   # the user's "things on yellow"


def test_legend_route_renders(client):
    page = client.get("/legend").text
    assert "Colour rules" in page
    assert "ISReq [PR/MP Review]" in page
    assert "Distractor" in page
    assert "Alerts (coloured by the handler's role)" in page
    assert "sw-yellow" in page   # yellow now visible (BVG/GEN alert rows)
    for role in ("PVG", "BVG", "GEN", "Project", "OFF"):
        assert role in page
