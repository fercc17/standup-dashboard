"""Highest-only focus toggle: ISReq non-priority → red Distractor (#86 follow-up)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from standup_dashboard.domain.roles import region_weekday
from tests.fixtures.jira_pd import Scenario, install, issue

COLIN = "colin.misare@canonical.com"  # AMER
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
            201: [
                # ps5 is green for GEN, so the Highest-only toggle is what demotes
                # it (a plain regular ISReq would already be a GEN distractor, #158).
                issue("ISReq-5", assignee=COLIN, status="In Progress", sprint_id=201,
                      labels=["ps5-blocker"]),
                issue("ISReq-6", assignee=COLIN, status="Untriaged", sprint_id=201),
            ],
        },
        users=[{"id": "PU1", "email": COLIN, "name": "Colin Misare"}],
    )


def test_highest_toggle_recolors_isreq_distractor_red(client, app, respx_mock):
    now = datetime.now(UTC)
    install(respx_mock, _scenario(now))
    slot = region_weekday(now, AMER_TZ)
    # GEN role: ISReq is NOT a role-distractor, so the toggle's effect is isolated
    # (Project would already force red regardless of the toggle).
    app.state.ctx.db.set_weekly_role(COLIN, slot, "GEN", now)
    client.post("/refresh", data={"regions": "AMER"})

    off = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    # Toggle off: the in-progress ISReq is current work in WIP, not a distraction.
    assert "ISReq-5" in off[: off.index("Distractors")]

    # Turn on "Highest only" → the non-Highest in-progress ISReq goes red in Distractors.
    assert client.post("/toggle/highest", data={"value": "on"}).status_code == 204
    on = client.get(f"/chip/{COLIN}/detail", params={"regions": "AMER"}).text
    assert "ISReq-5" in on and "c-red" in on
    assert "ISReq-5" in on[on.index("Distractors"):]
    # But an untriaged To Do ticket is never a distraction — it stays in To Do.
    assert "ISReq-6" in on[: on.index("Distractors")]
