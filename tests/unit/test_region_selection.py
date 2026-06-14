"""Region selection incl. deselect-all (#152)."""

from __future__ import annotations

from standup_dashboard import config
from standup_dashboard.web.routes import NO_REGION, _parse_regions, _region_links


def test_default_region_when_no_param():
    assert _parse_regions([]) == [config.REGION_KEYS[0]]


def test_explicit_none_marker_is_empty_selection():
    assert _parse_regions([NO_REGION]) == []


def test_parse_dedupes_preserving_order():
    a, b = config.REGION_KEYS[0], config.REGION_KEYS[1]
    assert _parse_regions([a, a, b]) == [a, b]


def test_deselecting_last_region_carries_none_marker():
    a, b = config.REGION_KEYS[0], config.REGION_KEYS[1]
    links = {x["key"]: x for x in _region_links([a])}
    # The one selected region's toggle link turns it OFF → explicit none marker.
    assert links[a]["active"] is True
    assert links[a]["href"].endswith(f"regions={NO_REGION}")
    # A non-selected region's link just adds it (and is inactive).
    assert links[b]["active"] is False
    assert f"regions={b}" in links[b]["href"]
