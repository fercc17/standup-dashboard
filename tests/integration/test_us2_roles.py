"""US2 integration: schedule persistence + strict-toggle recolor (T035)."""

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
            # Non-Highest, non-ps5 ISReq assigned to Colin → strict-mode sensitive.
            201: [issue("ISReq-5", assignee=COLIN, status="In Progress", sprint_id=201)],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )


def test_us2_persistence_and_strict_recolor(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    slot = region_weekday(now, AMER_TZ)

    # Persist a weekly default → BVG for today's slot.
    r = client.post(
        "/schedule/weekly", data={"engineer_email": COLIN, "weekday": slot, "role": "BVG"}
    )
    assert r.status_code == 200
    assert app.state.ctx.db.get_weekly_schedule()[(COLIN, slot)] == "BVG"

    # The strict toggle is now visible (a BVG engineer exists today).
    page = client.get("/", params={"regions": "AMER"}).text
    assert "BVG strict" in page

    client.post("/refresh", data={"regions": "AMER"})

    # Strict OFF (default): BVG ISReq non-priority → green.
    before = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISReq-5" in before and "c-green" in before

    # Toggle strict ON → recolors to yellow.
    toggled = client.post("/toggle/strict", data={"regions": "AMER", "value": "on"})
    assert toggled.status_code == 200
    after = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "c-yellow" in after
