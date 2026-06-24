"""DetailPanelVM per-column totals and 24H / Today / Pulse windows (#173/#cal)."""

from __future__ import annotations

from standup_dashboard.domain.models import CalendarAvail, DetailPanelVM, Role


def test_per_column_totals_sum_alert_union_jira_and_busy():
    vm = DetailPanelVM(
        email="e@x", name="E", role=Role.GEN,
        # pulse
        alert_union_seconds=3600, jira_project_seconds=1800, jira_request_seconds=600,
        # 24h
        alert_union_24h_seconds=300, jira_project_24h_seconds=120, jira_request_24h_seconds=0,
        # today
        alert_union_today_seconds=60, jira_project_today_seconds=0, jira_request_today_seconds=240,
        calendar=CalendarAvail(
            has_data=True,
            busy_seconds=7200, busy_today_seconds=1800, busy_24h_seconds=900),
    )
    # Each total = no-overlap alerts + Jira project + Jira ticket + busy, per window.
    assert vm.total_pulse_seconds == 3600 + 1800 + 600 + 7200
    assert vm.total_24h_seconds == 300 + 120 + 0 + 900
    assert vm.total_today_seconds == 60 + 0 + 240 + 1800
    # The overlap alert metric and `open` are deliberately NOT in the total.
    assert vm.total_pulse_label == "3h 40m"   # 13200s


def test_totals_zero_when_empty():
    vm = DetailPanelVM(email="e@x", name="E", role=Role.GEN)
    assert vm.total_pulse_seconds == vm.total_24h_seconds == vm.total_today_seconds == 0
    assert vm.total_today_label == "0m"


def test_distractor_share_is_percent_of_open_time():
    # 4h logged on distractors out of 20h open (non-busy) time → 20%.
    vm = DetailPanelVM(
        email="e@x", name="E", role=Role.GEN, show_distractors=True,
        distractor_seconds=4 * 3600,
        calendar=CalendarAvail(has_data=True, open_seconds=20 * 3600),
    )
    assert vm.distractor_share_label == "4h · 20% of open"


def test_distractor_share_blank_for_management_and_without_open_data():
    # Management never computes distractors → blank even with seconds set.
    mgr = DetailPanelVM(
        email="e@x", name="E", role=Role.GEN, show_distractors=False,
        distractor_seconds=4 * 3600,
        calendar=CalendarAvail(has_data=True, open_seconds=20 * 3600))
    assert mgr.distractor_share_label == ""
    # No open-time data to divide by → blank, not a divide-by-zero.
    nocal = DetailPanelVM(
        email="e@x", name="E", role=Role.GEN, show_distractors=True,
        distractor_seconds=4 * 3600)
    assert nocal.distractor_share_label == ""
