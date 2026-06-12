"""US2 integration: schedule persistence + role-based reclassification (T035, #86)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.roles import region_weekday
from tests.fixtures.jira_pd import Scenario, install, issue

COLIN = "colin.misare@canonical.com"  # AMER engineer
AMER_TZ = "America/Mexico_City"


def _jira_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _scenario(now: datetime) -> Scenario:
    start, end = now - timedelta(days=5), now + timedelta(days=5)
    return Scenario(
        boards={"ISDB": 1, "ISReq": 2},
        sprints={
            1: {"id": 101, "name": "ISDB", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
            2: {"id": 201, "name": "ISReq", "startDate": _jira_dt(start),
                "endDate": _jira_dt(end), "state": "active"},
        },
        sprint_issues={
            101: [],
            # Non-Highest, non-[PR/MP Review] ISReq assigned to Colin → for a BVG
            # this becomes a Distractor (#86).
            201: [issue("ISReq-5", assignee=COLIN, status="In Progress", sprint_id=201)],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )


def test_us2_persistence_and_role_reclassification(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    slot = region_weekday(now, AMER_TZ)

    # Persist a weekly default → BVG for today's slot.
    r = client.post(
        "/schedule/weekly", data={"engineer_email": COLIN, "weekday": slot, "role": "BVG"}
    )
    assert r.status_code == 200
    assert app.state.ctx.db.get_weekly_schedule()[(COLIN, slot)] == "BVG"

    client.post("/refresh", data={"regions": "AMER"})

    # BVG: a non-Highest / non-[PR/MP Review] assigned ISReq is reclassified to a
    # Distractor and colored red (#86) — no longer green.
    panel = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISReq-5" in panel
    assert "c-red" in panel
    assert "c-green" not in panel
    # It lands under the Distractors group, not WIP.
    assert "ISReq-5" in panel[panel.index("Distractors"):]
