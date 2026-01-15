"""Live smoke tests against the real APIs. Opt in with `pytest -m live`.

Kept separate from the offline suite because these depend on third-party availability and on what
disasters happen to be active — they answer "is the contract with each API still intact?", not "is
our logic correct". They are the tests that will catch an upstream API change.

Assertions are deliberately loose about *values* (a quiet week may have no Red-alert floods) and
strict about *shape* — response structure, units, and magnitudes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import rasterio

from app import config
from app.models import SourceUnavailable
from app.sources import cmr_stac, gdacs, worldpop

pytestmark = pytest.mark.live


def test_gdacs_event_list_responds():
    events = gdacs.list_flood_events(days=120, alert_levels=("Green", "Orange", "Red"))
    assert events, "GDACS returned no flood events in 120 days, which would be extraordinary"

    event = events[0]
    assert event.event_id > 0
    assert event.alert_level in gdacs.ALERT_LEVELS
    assert event.provenance.source == "gdacs"
    assert event.from_date is None or event.from_date.tzinfo is not None


def test_gdacs_event_list_bbox_is_still_a_centroid():
    """Documents the reason FloodEvent exposes no bbox. If GDACS ever ships real bboxes, this fails
    and the extent logic can be revisited."""
    payload, _prov = _raw_event_list()
    zero_area = 0
    for feature in payload["features"]:
        bbox = feature.get("bbox")
        if bbox and len(bbox) >= 4 and bbox[0] == bbox[2] and bbox[1] == bbox[3]:
            zero_area += 1
    assert zero_area > 0, "no zero-area centroid bboxes found — GDACS behavior may have changed"


def test_gdacs_geometry_yields_a_usable_extent():
    for event in gdacs.list_flood_events(days=120, alert_levels=("Orange", "Red")):
        if event.episode_id is None:
            continue
        try:
            extent = gdacs.get_flood_extent(event.event_id, event.episode_id)
        except SourceUnavailable:
            continue  # centroid-only events are legitimate; try the next one

        minx, miny, maxx, maxy = extent.bbox
        assert maxx > minx and maxy > miny
        assert -180 <= minx <= 180 and -90 <= miny <= 90
        assert extent.geometry["type"] in {"Polygon", "MultiPolygon"}

        # The derived bbox must contain the event's own centroid — the check that catches bbox math
        # errors, which would otherwise silently corrupt every population figure.
        if event.centroid:
            lon, lat = event.centroid
            assert minx <= lon <= maxx and miny <= lat <= maxy
        return

    pytest.skip("no event with an affected-area polygon available right now")


def test_cmr_stac_returns_geotiff_granules():
    """The default collection must still serve .tif, not .hdf."""
    result = cmr_stac.search_recent_flood_imagery([85.0, 26.5, 86.0, 27.5], days=7)

    assert result.granules
    assert any(g.data_format == "tif" for g in result.granules)
    for granule in result.granules:
        if granule.data_href:
            assert granule.data_href.startswith("https://")


def test_cmr_stac_historical_query_is_empty_and_says_so():
    """The NRT archive is short. If this ever starts returning data, the fallback logic can relax."""
    with pytest.raises(SourceUnavailable) as excinfo:
        cmr_stac.search_flood_imagery([66.0, 25.0, 70.0, 29.0], "2022-08-20", "2022-08-31")

    assert excinfo.value.recoverable is True


def test_gibs_tiles_serve_png_for_current_and_historical_dates():
    for when in (datetime.now(UTC) - timedelta(days=2), datetime(2022, 8, 28, tzinfo=UTC)):
        layer = cmr_stac.gibs_tile_layer(when)
        response = httpx.get(cmr_stac.example_tile_url(layer), timeout=60)

        assert response.status_code == 200, f"{layer.date}: HTTP {response.status_code}"
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 100


def test_worldpop_un_adjusted_totals_match_un_figures():
    """The check that caught the 38% overstatement in the unconstrained product.

    Summing a whole country's raster should land near its known population.
    """
    expected = {"NPL": 29_100_000, "PAK": 220_000_000}

    for iso3, un_figure in expected.items():
        url, _record, alias, _prov = worldpop.resolve_dataset(iso3)
        assert alias == worldpop.UN_ADJUSTED_ALIAS
        assert url.endswith(".tif")

        path, _prov = worldpop.download_raster(url)
        with rasterio.open(path) as src:
            assert src.crs.to_epsg() == 4326
            values = src.read(1)

        total = float(values[(values >= 0)].sum())
        ratio = total / un_figure
        assert 0.9 < ratio < 1.1, f"{iso3}: raster total {total:,.0f} vs UN {un_figure:,.0f}"


def test_worldpop_population_over_a_real_flood_polygon():
    for event in gdacs.list_flood_events(days=120, alert_levels=("Orange", "Red")):
        if event.episode_id is None:
            continue
        try:
            extent = gdacs.get_flood_extent(event.event_id, event.episode_id)
            codes = [c.iso3 for c in event.affected_countries if c.iso3] or [event.iso3]
            estimate = worldpop.population_in_geometry(
                extent.geometry, [c for c in codes if c], event_date=event.from_date
            )
        except SourceUnavailable:
            continue

        assert estimate.population >= 0
        assert estimate.pixels_counted > 0
        # No country can exceed world population; a wild figure means the geometry or CRS is wrong.
        assert estimate.population < 8_000_000_000
        assert estimate.provenance.source == "worldpop"
        return

    pytest.skip("no event with both a polygon and population coverage available right now")


def test_end_to_end_report_builds():
    events = gdacs.list_flood_events(days=120, alert_levels=("Orange", "Red"))
    if not events:
        pytest.skip("no active Orange/Red flood events")

    from app import report as report_module

    result = report_module.build_event_report(events[0].event_id, events[0].episode_id)
    summary = report_module.report_summary(result)

    assert summary["event_id"] == events[0].event_id
    # Every present figure carries a source...
    assert summary["sources"]
    assert "tile_template" in summary  # always available, so always expected

    # ...and every figure is either reported or explained. Never silently absent.
    assert ("population" in summary) ^ ("population" in summary["unavailable"])
    assert ("granules" in summary) ^ ("imagery" in summary["unavailable"])


def _raw_event_list():
    from app.fetch import fetch_json

    today = datetime.now(UTC).date()
    return fetch_json(
        "gdacs",
        config.GDACS_EVENTLIST_URL,
        {
            "eventlist": "FL",
            "fromdate": (today - timedelta(days=120)).isoformat(),
            "todate": today.isoformat(),
            "alertlevel": "Green;Orange;Red",
        },
        ttl=config.TTL_GDACS_EVENTS,
    )
