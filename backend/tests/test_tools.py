"""Tool-layer semantics that the agents and MCP clients both depend on.

The event-list cap gets the attention here because it is the one place in the tool layer that
*discards* data. A cap that cut the wrong events, or cut them quietly, would be invisible in the UI
and would look exactly like "these are all the events there are".
"""

from __future__ import annotations

from app import tools
from app.sources import gdacs


def test_cap_keeps_the_most_severe_not_the_first_listed(fixture_fetch):
    """The committed fixture lists Orange (1103339) *before* Red (1103400).

    GDACS does not order by severity, so a naive `events[:max_events]` would keep the Orange event
    and silently drop the Red one — the worst possible way for a cap to fail in this system.
    """
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    result = tools.tool_list_flood_events(days=30, alert_levels=["Orange", "Red"], max_events=1)

    assert result["ok"] is True
    assert [e["event_id"] for e in result["events"]] == [1103400]
    assert result["events"][0]["alert_level"] == "Red"


def test_events_are_returned_most_urgent_first(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    result = tools.tool_list_flood_events(days=30, alert_levels=["Orange", "Red"])

    assert [e["alert_level"] for e in result["events"]] == ["Red", "Orange"]


def test_truncation_is_reported_never_silent(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    result = tools.tool_list_flood_events(days=30, alert_levels=["Orange", "Red"], max_events=1)

    assert result["truncated"] is True
    assert result["total_matched"] == 2
    assert result["event_count"] == 1
    assert result["max_events"] == 1
    assert "2 events matched" in result["note"]


def test_untruncated_result_still_reports_total_matched(fixture_fetch):
    """`total_matched` is always present, so a caller never has to infer whether a cap applied."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    result = tools.tool_list_flood_events(days=30, alert_levels=["Orange", "Red"], max_events=10)

    assert result["total_matched"] == 2
    assert result["event_count"] == 2
    assert "truncated" not in result


def test_max_events_zero_disables_the_cap(fixture_fetch):
    """`alert_monitor` passes 0 for a direct event lookup, so this path must return everything."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    result = tools.tool_list_flood_events(days=30, alert_levels=["Orange", "Red"], max_events=0)

    assert result["event_count"] == 2
    assert "truncated" not in result


def test_empty_result_is_unavailable_not_an_empty_list(fixture_fetch):
    """An empty window is a real answer, and the tool contract reports it as unavailable-recoverable."""
    fixture_fetch(gdacs, payload={"type": "FeatureCollection", "features": []})
    result = tools.tool_list_flood_events(days=30, max_events=10)

    assert result["ok"] is False
    assert result["unavailable"] is True
    assert result["recoverable"] is True
    assert "no flood events" in result["reason"]
