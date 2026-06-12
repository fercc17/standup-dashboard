"""US2 integration: transposed per-region schedule modal + paste import (#71)."""

from __future__ import annotations

COLIN = "colin.misare@canonical.com"
NICK = "nikolaos.sakkos@canonical.com"


def test_schedule_modal_is_per_region_and_excludes_management(client):
    page = client.get("/schedule").text
    # Per-region sections.
    assert "AMER" in page and "APAC" in page and "EMEA" in page
    # Engineers are now column headers; management is excluded (#72/#71).
    assert "Colin Misare" in page
    assert "Fernando Carrillo" not in page
    # Paste box, the transposed override row, and the weekend note are present.
    assert "Paste from spreadsheet" in page
    assert "Today override" in page
    assert "Weekend" in page


def test_schedule_paste_applies_and_rerenders(client, app):
    text = "Date\tColin\tNick\nMon, Jun 08\tBVG\tPS7+\n"
    resp = client.post("/schedule/paste", data={"paste": text})
    assert resp.status_code == 200
    assert "Applied 2 role(s) and 1 note(s)" in resp.text
    db = app.state.ctx.db
    assert db.get_weekly_schedule()[(COLIN, "MON")] == "BVG"
    assert db.get_weekly_schedule()[(NICK, "MON")] == "Project"  # PS7+ → Project
    assert db.get_day_notes()[(NICK, "MON")] == "PS7+"
    assert "PS7+" in resp.text  # the note shows in the re-rendered modal
