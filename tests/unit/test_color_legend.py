"""Colour-rule legend matrix (#143) — derived live from domain/coloring.py."""

from __future__ import annotations

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


def test_legend_route_renders(client):
    page = client.get("/legend").text
    assert "Colour rules" in page
    assert "ISReq [PR/MP Review]" in page
    assert "Distractor" in page
    for role in ("PVG", "BVG", "GEN", "Project", "OFF"):
        assert role in page
