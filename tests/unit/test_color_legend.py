"""Colour-rule legend matrix (#143) — derived live from domain/coloring.py."""

from __future__ import annotations

from standup_dashboard.domain.coloring import alert_color
from standup_dashboard.domain.models import Color, Role, TicketGroup
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


def test_alert_color_rules():
    # General case (non-GEN): resolved green, acked-recent yellow, acked-stale red.
    assert alert_color(Role.PVG, resolved=True, recent=False) == (Color.GREEN, TicketGroup.SUCCESS)
    assert alert_color(Role.PVG, resolved=False, recent=True) == (Color.YELLOW, TicketGroup.WIP)
    assert alert_color(Role.PVG, resolved=False, recent=False) == (Color.RED, TicketGroup.WIP)
    # GEN: alerts are a distraction — resolved yellow, unresolved red.
    assert alert_color(Role.GEN, resolved=True, recent=False) == (Color.YELLOW, TicketGroup.DISTRACTORS)
    assert alert_color(Role.GEN, resolved=False, recent=True) == (Color.RED, TicketGroup.DISTRACTORS)
    # A management GEN is treated as the general case, not GEN-distraction.
    assert alert_color(Role.GEN, resolved=True, recent=False, is_management=True) == (
        Color.GREEN, TicketGroup.SUCCESS)


def test_legend_includes_alerts_with_yellow():
    legend = build_color_legend()
    assert legend["alert_cols"] == ["Most roles", "GEN"]
    states = {r["state"]: r["cells"] for r in legend["alert_rows"]}
    assert states["Resolved"][0] == {"color": "green", "group": "Success"}
    assert states["Acknowledged · today"][0] == {"color": "yellow", "group": "WIP"}
    assert states["Acknowledged · >24h (stale)"][0] == {"color": "red", "group": "WIP"}
    assert states["Resolved"][1] == {"color": "yellow", "group": "Distractors"}   # GEN
    # The user's "things on yellow" must actually appear.
    all_alert_colours = {c["color"] for cells in states.values() for c in cells}
    assert "yellow" in all_alert_colours


def test_legend_route_renders(client):
    page = client.get("/legend").text
    assert "Colour rules" in page
    assert "ISReq [PR/MP Review]" in page
    assert "Distractor" in page
    assert "Alerts (your own PagerDuty incidents)" in page
    assert "sw-yellow" in page   # yellow now visible (alerts table)
    for role in ("PVG", "BVG", "GEN", "Project", "OFF"):
        assert role in page
