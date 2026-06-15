"""Colour-rule legend matrix (#158) — derived live from domain/coloring.py."""

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

    # PVG: everything distracts — yellow, except regular (red).
    pvg = rows["PVG"]
    assert pvg[0] == {"color": "yellow", "distractor": True}   # Highest
    assert pvg[3] == {"color": "red", "distractor": True}      # regular
    assert pvg[4] == {"color": "yellow", "distractor": True}   # ISDB
    # OFF: all red distractors.
    assert all(c == {"color": "red", "distractor": True} for c in rows["OFF"])
    # BVG: Highest / PR-MP / ps5 green; regular + ISDB red distractors.
    bvg = rows["BVG"]
    assert bvg[0] == {"color": "green", "distractor": False}   # Highest
    assert bvg[2] == {"color": "green", "distractor": False}   # ps5 (now green)
    assert bvg[3] == {"color": "red", "distractor": True}      # regular
    assert bvg[4] == {"color": "red", "distractor": True}      # ISDB
    # GEN: Highest / ps5 green; PR-MP yellow distractor; regular + ISDB red.
    gen = rows["GEN"]
    assert gen[0]["color"] == "green"
    assert gen[1] == {"color": "yellow", "distractor": True}   # PR-MP
    assert gen[2]["color"] == "green"
    assert gen[3] == {"color": "red", "distractor": True}      # regular (now distractor)
    assert gen[4] == {"color": "red", "distractor": True}      # ISDB
    # Project: only ISDB is green; ISReq work is a red distractor.
    proj = rows["Project"]
    assert proj[4] == {"color": "green", "distractor": False}  # ISDB
    assert proj[0] == {"color": "red", "distractor": True}     # ISReq Highest


def test_alert_color_representative():
    # Representative alert colour (a yellow open alert) per role.
    assert alert_color(Role.PVG) is Color.YELLOW
    assert alert_color(Role.BVG) is Color.YELLOW
    assert alert_color(Role.GEN) is Color.RED
    assert alert_color(Role.PROJECT) is Color.RED
    assert alert_color(Role.OFF) is Color.RED


def test_legend_alert_states_per_role():
    by_role = {r["role"]: r["states"] for r in build_color_legend()["alert_rows"]}
    # PVG: state+age dependent — green resolved, yellow ≤24h, red >24h.
    assert [s["color"] for s in by_role["PVG"]] == ["green", "yellow", "red"]
    # BVG: a single yellow (open or resolved).
    assert [s["color"] for s in by_role["BVG"]] == ["yellow"]
    # GEN / Project / OFF: a single red distraction.
    for role in ("GEN", "Project", "OFF"):
        assert [s["color"] for s in by_role[role]] == ["red"]


def test_legend_route_renders(client):
    page = client.get("/legend").text
    assert "Colour rules" in page
    assert "ISReq [PR/MP Review]" in page
    assert "Distractor" in page
    assert "Handled alert" in page   # the combined matrix's alert column
    assert "sw-yellow" in page
    for role in ("PVG", "BVG", "GEN", "Project", "OFF"):
        assert role in page
