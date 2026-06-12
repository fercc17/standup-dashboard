"""ChipGroup splits region chips into Operations / Project sub-groups."""

from __future__ import annotations

from standup_dashboard.domain.models import ChipVM, Role
from standup_dashboard.web.presenters import ChipGroup


def _chip(role: Role) -> ChipVM:
    return ChipVM(email="e@x", name="E", role=role, is_manager=False,
                  touched_24h=0, alerts_ack_24h=0, alerts_resolved_24h=0, region_key="AMER")


def test_chipgroup_splits_operations_and_project():
    g = ChipGroup(key="AMER", label="AMER", local_day="x", chips=[
        _chip(Role.PVG), _chip(Role.OFF), _chip(Role.GEN),
        _chip(Role.PROJECT), _chip(Role.BVG),
    ])
    assert [c.role for c in g.ops_chips] == [Role.PVG, Role.GEN, Role.BVG]
    assert [c.role for c in g.project_chips] == [Role.OFF, Role.PROJECT]
