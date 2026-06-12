"""US2 integration: redesigned per-region schedule modal + paste import (#71)."""

from __future__ import annotations

COLIN = "colin.misare@canonical.com"


def test_schedule_modal_is_per_region_dated_and_excludes_management(client):
    page = client.get("/schedule").text
    # Per-region sections.
    assert "AMER" in page and "APAC" in page and "EMEA" in page
    # A real engineer shows; management is excluded from the schedule (#72/#71).
    assert "Colin Misare" in page
    assert "Fernando Carrillo Castro" not in page
    # Dated headers, the paste box, and the weekend-has-no-role note are present.
    assert "Paste from spreadsheet" in page
    assert "Today override" in page
    assert "Weekend" in page


def test_schedule_paste_applies_and_rerenders(client, app):
    text = "Date\tColin Misare\nMon, Jun 08\tBVG | review day\n"
    resp = client.post("/schedule/paste", data={"paste": text})
    assert resp.status_code == 200
    assert "Applied 1 role(s) and 1 note(s)" in resp.text
    # Persisted, and the re-rendered modal reflects the new note.
    assert app.state.ctx.db.get_weekly_schedule()[(COLIN, "MON")] == "BVG"
    assert app.state.ctx.db.get_day_notes()[(COLIN, "MON")] == "review day"
    assert "review day" in resp.text
