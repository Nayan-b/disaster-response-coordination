"""GDACS parsing and extent-derivation tests.

These target the specific ways GDACS data can mislead, all observed in live responses.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from app.models import SourceUnavailable
from app.sources import gdacs


def test_parses_events_and_skips_malformed(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    events = gdacs.list_flood_events(days=30)

    # The third fixture feature has no eventid and must be skipped rather than crash the parse.
    assert len(events) == 2
    assert {e.event_id for e in events} == {1103339, 1103400}


def test_keeps_overall_and_episode_alert_levels_separate(fixture_fetch):
    """An event can be overall Orange while its latest episode is Green."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    event = next(e for e in gdacs.list_flood_events(days=30) if e.event_id == 1103339)

    assert event.alert_level == "Orange"
    assert event.episode_alert_level == "Green"
    assert event.alert_score == 2
    assert event.episode_alert_score == 0.5


def test_event_has_no_bbox_field(fixture_fetch):
    """The event list's bbox is a zero-area centroid, so FloodEvent must not expose one.

    Guards against a future change reintroducing it: population computed over a zero-area bbox would
    be silently meaningless.
    """
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    event = gdacs.list_flood_events(days=30)[0]
    assert not hasattr(event, "bbox")


def test_naive_timestamps_become_utc(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    event = next(e for e in gdacs.list_flood_events(days=30) if e.event_id == 1103339)

    assert event.from_date is not None
    assert event.from_date.tzinfo is not None
    assert event.from_date.utcoffset().total_seconds() == 0
    assert event.from_date.year == 2025 and event.from_date.month == 6


def test_string_booleans_are_parsed(fixture_fetch):
    """GDACS sends iscurrent/istemporary as the strings 'true'/'false', not JSON booleans."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    events = {e.event_id: e for e in gdacs.list_flood_events(days=30)}

    assert events[1103339].is_current is False
    assert events[1103339].is_temporary is False
    assert events[1103400].is_current is True
    assert events[1103400].is_temporary is True


def test_zero_severity_is_preserved_not_dropped(fixture_fetch):
    """severity 0.0 means GDACS left the field unpopulated — it must survive as 0.0, not None."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    event = next(e for e in gdacs.list_flood_events(days=30) if e.event_id == 1103339)

    assert event.severity_value == 0.0
    assert event.severity_text == "Magnitude 0 "


def test_multi_country_events_keep_all_codes(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    event = next(e for e in gdacs.list_flood_events(days=30) if e.event_id == 1103400)

    assert [c.iso3 for c in event.affected_countries] == ["SSD", "ETH"]


def test_events_sorted_by_most_recently_modified(fixture_fetch):
    """A responder cares about what just changed, so the newest modification comes first."""
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    events = gdacs.list_flood_events(days=30)

    # 1103339 modified 2025-10-08; 1103400 modified 2025-08-02.
    assert [e.event_id for e in events] == [1103339, 1103400]


def test_alert_level_filter_is_semicolon_separated(fixture_fetch):
    calls = fixture_fetch(gdacs, "gdacs_eventlist.json")
    gdacs.list_flood_events(days=30, alert_levels=("Orange", "Red"))

    _url, params = calls[0]
    assert params["alertlevel"] == "Orange;Red"
    assert params["eventlist"] == "FL"


def test_unknown_alert_level_rejected(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    with pytest.raises(ValueError, match="unknown alert level"):
        gdacs.list_flood_events(days=30, alert_levels=("Purple",))


def test_requires_a_date_range():
    with pytest.raises(ValueError, match="from_date/to_date or days"):
        gdacs.list_flood_events()


def test_rejects_non_featurecollection(fixture_fetch):
    fixture_fetch(gdacs, payload={"unexpected": "shape"})
    with pytest.raises(SourceUnavailable, match="FeatureCollection"):
        gdacs.list_flood_events(days=30)


# --- extent derivation ------------------------------------------------------


def test_affected_polygon_selected_by_class_not_position(fixture_fetch):
    """The fixture puts the centroid first and Poly_Global before Poly_Affected.

    A positional implementation would return the centroid or the far larger global-context polygon.
    """
    fixture_fetch(gdacs, "gdacs_geometry.json")
    extent = gdacs.get_flood_extent(1103933, 18)

    assert extent.polygon_label == "Affected area"
    assert extent.geometry["type"] == "MultiPolygon"
    # The affected area, not the 90-115 / 20-45 global polygon.
    assert extent.bbox == (102.5, 34.9, 103.9, 36.2)


def test_bbox_is_derived_and_has_area(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_geometry.json")
    extent = gdacs.get_flood_extent(1103933, 18)

    minx, miny, maxx, maxy = extent.bbox
    assert maxx > minx and maxy > miny
    assert extent.area_sq_deg > 0
    assert extent.bbox_string == "102.5,34.9,103.9,36.2"


def test_centroid_only_event_raises_rather_than_buffering(fixture_fetch):
    """No affected polygon means no extent. Inventing one would corrupt every downstream figure."""
    fixture_fetch(gdacs, "gdacs_geometry_centroid_only.json")

    with pytest.raises(SourceUnavailable) as excinfo:
        gdacs.get_flood_extent(1103400, 2)

    assert "Poly_Affected" in excinfo.value.reason
    assert excinfo.value.recoverable is True


def test_empty_geometry_response_raises(fixture_fetch):
    fixture_fetch(gdacs, payload={"type": "FeatureCollection", "features": []})
    with pytest.raises(SourceUnavailable, match="no geometry features"):
        gdacs.get_flood_extent(1, 1)


def test_zero_area_polygon_rejected(fixture_fetch):
    """A degenerate polygon collapsing to a line has no usable extent."""
    fixture_fetch(
        gdacs,
        payload={
            "type": "FeatureCollection",
            "features": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[10.0, 5.0], [10.0, 6.0], [10.0, 5.0]]],
                    },
                    "properties": {"Class": "Poly_Affected", "polygonlabel": "Affected area"},
                }
            ],
        },
    )
    with pytest.raises(SourceUnavailable, match="zero-area"):
        gdacs.get_flood_extent(1, 1)


def test_falls_back_to_polygon_label(fixture_fetch):
    """If the Class property is missing, the human-readable label is the next-best selector."""
    fixture_fetch(
        gdacs,
        payload={
            "type": "FeatureCollection",
            "features": [
                {
                    "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
                    "properties": {"polygonlabel": "Centroid"},
                },
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]],
                    },
                    "properties": {"polygonlabel": "Affected area"},
                },
            ],
        },
    )
    extent = gdacs.get_flood_extent(1, 1)
    assert extent.bbox == (1.0, 1.0, 2.0, 2.0)


def test_geometry_request_uses_event_and_episode(fixture_fetch):
    calls = fixture_fetch(gdacs, "gdacs_geometry.json")
    gdacs.get_flood_extent(1103933, 18)

    _url, params = calls[0]
    assert params == {"eventtype": "FL", "eventid": 1103933, "episodeid": 18}


def test_get_event_reports_miss_clearly(fixture_fetch):
    fixture_fetch(gdacs, "gdacs_eventlist.json")
    with pytest.raises(SourceUnavailable) as excinfo:
        gdacs.get_event(999999)

    assert "not found" in excinfo.value.reason
    assert excinfo.value.recoverable is True
