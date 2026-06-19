"""Automatic pulse rollover (#144).

Verifies the two claims behind "do we auto-jump to a new sprint/pulse?":
  1. ``current_pulse`` advances by the calendar — crossing a boundary bumps the
     number with no manual step.
  2. When we move into the next pulse, the prior pulse's totals stay frozen in
     pulse history (the previous-pulse gap-fill is replace=False, so a later
     refresh whose live window no longer covers it can't wipe it to zero), and
     the new pulse starts as its own clean-slate row.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from standup_dashboard.domain.models import Alert, AlertState, Pulse
from standup_dashboard.services.counts import persist_pulse_summaries
from standup_dashboard.services.pulse import current_pulse
from standup_dashboard.storage.db import Database

MEMBER = "alexandre.gomes@canonical.com"  # AMER


def _pulse(num, start, end):
    return Pulse("ISReq", num, "s",
                 datetime.combine(start, time(), UTC), datetime.combine(end, time(), UTC))


def test_current_pulse_advances_across_boundary():
    n_a, _, end_a = current_pulse(datetime(2026, 6, 13, tzinfo=UTC).date())
    n_b, start_b, _ = current_pulse(end_a)          # first day of the next pulse
    assert n_b == n_a + 1 and start_b == end_a       # contiguous, auto-advanced


def test_prior_pulse_frozen_when_rolled_over(tmp_path, db_dsn):
    db = Database(db_dsn)

    # --- During pulse N: a refresh persists N (current) with one ack'd alert. ---
    now_n = datetime(2026, 6, 13, 18, tzinfo=UTC)
    n, s_n, e_n = current_pulse(now_n.date())
    alert = Alert("INC1", MEMBER, AlertState.ACKNOWLEDGED, datetime(2026, 6, 12, 18, tzinfo=UTC))
    persist_pulse_summaries(db, [], [alert], [_pulse(1, s_n, e_n)], now_n)
    stored = {(p, r): c for p, r, c, _ in db.get_pulse_summaries()}
    assert stored[(n, "AMER")]["alerts_ack"] == 1

    # --- We roll into pulse N+1: a refresh with NO alerts persists N+1 (clean
    #     slate) and gap-fills N as previous. N must keep its frozen ack count. ---
    now_next = datetime.combine(e_n, time(18), UTC)
    n2, s2, e2 = current_pulse(now_next.date())
    assert n2 == n + 1
    persist_pulse_summaries(db, [], [], [_pulse(2, s2, e2)], now_next)

    after = {(p, r): c for p, r, c, _ in db.get_pulse_summaries()}
    assert after[(n, "AMER")]["alerts_ack"] == 1     # prior pulse frozen, not wiped
    assert (n2, "AMER") in after                     # new pulse present as its own row
    assert after[(n2, "AMER")]["alerts_ack"] == 0    # clean slate
    db.close()
