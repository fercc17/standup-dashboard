"""Deselect-all regions (#152) + server-persisted Management toggle (#151)."""

from __future__ import annotations

from standup_dashboard.services import schedule


def test_can_deselect_all_regions(client):
    # The explicit none marker renders a region-less view (no region button active),
    # rather than falling back to the first region.
    r = client.get("/?regions=none")
    assert r.status_code == 200
    assert 'region-btn active' not in r.text   # no AMER/APAC/EMEA selected
    # Sanity: the default (no marker) DOES select the first region.
    assert 'region-btn active' in client.get("/").text


def test_management_toggle_is_server_persisted(client, app):
    db = app.state.ctx.db
    assert schedule.get_show_management(db) is True            # default on
    r = client.post("/toggle/management", data={"regions": "AMER"})
    assert r.status_code == 200
    assert schedule.get_show_management(db) is False           # persisted off
    assert 'id="management-group"' not in r.text               # group hidden
    r = client.post("/toggle/management", data={"regions": "AMER"})
    assert schedule.get_show_management(db) is True            # back on
