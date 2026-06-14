"""Aging-WIP modal route (#147)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.models import Ticket

AMER_MEMBER = "alexandre.gomes@canonical.com"


def test_aging_wip_modal_lists_member_wip(client, app):
    db = app.state.ctx.db
    now = datetime.now(UTC)
    f = db.create_fetch_snapshot(fetched_at=now, jira_ok=True, pagerduty_ok=True,
                                 ical_ok=True, raw_path="")
    db.insert_tickets(f, [
        Ticket(id="ISReq-9", project_key="ISReq", title="stuck for ages", status="In Progress",
               priority=None, assignee_email=AMER_MEMBER, wip_since=now - timedelta(days=6)),
        Ticket(id="ISReq-8", project_key="ISReq", title="just started", status="In Progress",
               priority=None, assignee_email=AMER_MEMBER, wip_since=now - timedelta(hours=3)),
        Ticket(id="ISReq-7", project_key="ISReq", title="done", status="Done",
               priority=None, assignee_email=AMER_MEMBER),
    ])
    r = client.get("/tickets/aging-wip", params={"regions": "AMER"})
    assert r.status_code == 200
    body = r.text
    assert "ISReq-9" in body and "ISReq-8" in body   # both WIP tickets listed
    assert "ISReq-7" not in body                       # the Done ticket is excluded
    assert "lvl-red" in body                           # the 6-day-old one is red
    # Oldest first: ISReq-9 appears before ISReq-8.
    assert body.index("ISReq-9") < body.index("ISReq-8")
