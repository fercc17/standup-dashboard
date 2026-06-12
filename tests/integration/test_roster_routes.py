"""Roster editing via the web routes (#16)."""

from __future__ import annotations


def test_add_engineer_via_route_shows_on_dashboard(client):
    resp = client.post(
        "/roster/add",
        data={"name": "New Person", "email": "new.person@canonical.com", "region": "EMEA"},
    )
    assert resp.status_code == 200
    assert "new.person@canonical.com" in resp.text  # re-rendered roster modal
    page = client.get("/", params={"regions": "EMEA"}).text
    assert "New Person" in page


def test_move_engineer_via_route(client):
    client.post(
        "/roster/move",
        data={"email": "alexandre.gomes@canonical.com", "region": "EMEA"},
    )
    amer = client.get("/", params={"regions": "AMER"}).text
    emea = client.get("/", params={"regions": "EMEA"}).text
    assert "Alexandre Gomes" not in amer
    assert "Alexandre Gomes" in emea


def test_add_validation_shows_error(client):
    resp = client.post("/roster/add", data={"name": "X", "email": "bad", "region": "AMER"})
    assert resp.status_code == 200
    assert "invalid email" in resp.text
