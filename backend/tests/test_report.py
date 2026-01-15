"""Report-assembly tests.

The behavior under test is the guardrail contract: a failing source leaves its slot empty and records
why, so no figure is ever silently substituted or quietly dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import report as report_module
from app.models import FloodEvent, Provenance, SourceUnavailable
from app.sources import cmr_stac, gdacs, worldpop


def _event(**overrides) -> FloodEvent:
    defaults = dict(
        event_id=1103933,
        episode_id=18,
        name="Flood in China",
        country="China",
        iso3="CHN",
        alert_level="Orange",
        episode_alert_level="Green",
        from_date=datetime(2026, 6, 6, tzinfo=UTC),
        to_date=datetime(2026, 7, 14, tzinfo=UTC),
        provenance=Provenance(
            source="gdacs",
            request_url="https://x.test/events",
            fetched_at=datetime(2026, 8, 14, tzinfo=UTC),
        ),
    )
    defaults.update(overrides)
    return FloodEvent(**defaults)


@pytest.fixture
def stub_sources(monkeypatch, request):
    """Install stubs for every source; individual tests override what should fail."""
    monkeypatch.setattr(gdacs, "get_event", lambda *a, **k: _event())
    return monkeypatch


def test_imagery_reference_date_clamped_to_today():
    """GDACS publishes forward-looking windows; asking for tomorrow's imagery returns blank tiles."""
    future = datetime.now(UTC) + timedelta(days=30)
    event = _event(to_date=future)

    assert report_module.imagery_reference_date(event) <= datetime.now(UTC)


def test_past_event_window_is_respected():
    """A closed event window is used as-is — imagery should match the flood, not today."""
    event = _event(to_date=datetime(2026, 7, 14, tzinfo=UTC))
    assert report_module.imagery_reference_date(event) == datetime(2026, 7, 14, tzinfo=UTC)


def test_missing_extent_reports_one_root_cause(stub_sources, monkeypatch):
    """Imagery and population both depend on the extent; report the single reason, not three."""

    def fail_extent(*a, **k):
        raise SourceUnavailable("gdacs", "only a centroid published", "https://x.test", recoverable=True)

    monkeypatch.setattr(gdacs, "get_flood_extent", fail_extent)
    result = report_module.build_event_report(1103933, 18)

    assert result.extent is None
    assert "centroid" in result.unavailable["extent"]
    assert "no flood extent" in result.unavailable["imagery"]
    assert "no flood extent" in result.unavailable["population"]
    # Nothing was invented in place of the missing data.
    assert result.population is None and result.imagery is None


def test_missing_episode_id_is_reported(stub_sources, monkeypatch):
    monkeypatch.setattr(gdacs, "get_event", lambda *a, **k: _event(episode_id=None))
    result = report_module.build_event_report(1103933, None)

    assert "episode id" in result.unavailable["extent"]


def test_imagery_failure_does_not_block_population(stub_sources, monkeypatch, population_raster):
    """The NRT archive is short; a missing granule must not cost the responder a population figure."""
    extent = _extent()
    monkeypatch.setattr(gdacs, "get_flood_extent", lambda *a, **k: extent)

    def fail_imagery(*a, **k):
        raise SourceUnavailable("cmr_stac", "no granules in range", "https://x.test", recoverable=True)

    monkeypatch.setattr(cmr_stac, "search_flood_imagery", fail_imagery)
    monkeypatch.setattr(
        worldpop, "population_in_geometry", lambda *a, **k: _population_estimate()
    )

    result = report_module.build_event_report(1103933, 18)

    assert result.imagery is None
    assert "no granules" in result.unavailable["imagery"]
    assert result.population is not None
    # Tiles still cover the gap.
    assert result.tiles is not None


def test_tiles_are_always_present(stub_sources, monkeypatch):
    """The fallback must not depend on a network call that can fail."""
    monkeypatch.setattr(gdacs, "get_flood_extent", lambda *a, **k: _extent())
    monkeypatch.setattr(
        cmr_stac,
        "search_flood_imagery",
        lambda *a, **k: (_ for _ in ()).throw(
            SourceUnavailable("cmr_stac", "nothing", "https://x.test")
        ),
    )
    monkeypatch.setattr(
        worldpop,
        "population_in_geometry",
        lambda *a, **k: (_ for _ in ()).throw(SourceUnavailable("worldpop", "nothing", "x")),
    )

    result = report_module.build_event_report(1103933, 18)
    assert result.tiles is not None
    assert result.unavailable["population"] == "nothing"


def test_event_without_country_code_cannot_get_population(stub_sources, monkeypatch):
    monkeypatch.setattr(gdacs, "get_event", lambda *a, **k: _event(iso3=None, affected_countries=[]))
    monkeypatch.setattr(gdacs, "get_flood_extent", lambda *a, **k: _extent())
    monkeypatch.setattr(
        cmr_stac,
        "search_flood_imagery",
        lambda *a, **k: (_ for _ in ()).throw(SourceUnavailable("cmr_stac", "n", "x")),
    )

    result = report_module.build_event_report(1103933, 18)
    assert "no affected country code" in result.unavailable["population"]


def test_provenance_records_cover_every_present_figure(stub_sources, monkeypatch):
    monkeypatch.setattr(gdacs, "get_flood_extent", lambda *a, **k: _extent())
    monkeypatch.setattr(
        cmr_stac,
        "search_flood_imagery",
        lambda *a, **k: (_ for _ in ()).throw(SourceUnavailable("cmr_stac", "n", "x")),
    )
    monkeypatch.setattr(worldpop, "population_in_geometry", lambda *a, **k: _population_estimate())

    result = report_module.build_event_report(1103933, 18)
    named = dict(result.provenance_records())

    # Every figure that exists is traceable; nothing absent is listed.
    assert "event" in named and "flood extent / bbox" in named
    assert "population" in named and "tile layer" in named
    assert "imagery granules" not in named


def test_summary_omits_unavailable_figures(stub_sources, monkeypatch):
    """Anything absent from the summary is something no downstream agent may assert."""
    monkeypatch.setattr(gdacs, "get_flood_extent", lambda *a, **k: _extent())
    monkeypatch.setattr(
        cmr_stac,
        "search_flood_imagery",
        lambda *a, **k: (_ for _ in ()).throw(SourceUnavailable("cmr_stac", "no granules", "x")),
    )
    monkeypatch.setattr(
        worldpop,
        "population_in_geometry",
        lambda *a, **k: (_ for _ in ()).throw(SourceUnavailable("worldpop", "no raster", "x")),
    )

    summary = report_module.report_summary(report_module.build_event_report(1103933, 18))

    assert "population" not in summary
    assert "granules" not in summary
    assert summary["unavailable"]["population"] == "no raster"
    assert summary["event_id"] == 1103933


def test_event_lookup_failure_is_fatal(monkeypatch):
    """Without the event there is nothing to report on, so this one does propagate."""

    def fail(*a, **k):
        raise SourceUnavailable("gdacs", "event not found", "https://x.test", recoverable=True)

    monkeypatch.setattr(gdacs, "get_event", fail)
    with pytest.raises(SourceUnavailable, match="event not found"):
        report_module.build_event_report(999999)


# --- helpers ----------------------------------------------------------------


def _extent():
    from app.models import FloodExtent

    return FloodExtent(
        event_id=1103933,
        episode_id=18,
        geometry={
            "type": "Polygon",
            "coordinates": [[[102.5, 34.9], [103.9, 34.9], [103.9, 36.2], [102.5, 36.2], [102.5, 34.9]]],
        },
        bbox=(102.5, 34.9, 103.9, 36.2),
        area_sq_deg=1.82,
        polygon_label="Affected area",
        provenance=Provenance(
            source="gdacs",
            request_url="https://x.test/geometry",
            fetched_at=datetime(2026, 8, 14, tzinfo=UTC),
        ),
    )


def _population_estimate():
    from app.models import PopulationEstimate

    return PopulationEstimate(
        population=2_063_552.0,
        iso3_covered=["CHN"],
        raster_year=2020,
        resolution="1km",
        pixels_counted=11_664,
        nodata_pixels_skipped=86,
        per_country={"CHN": 2_063_552.0},
        provenance=Provenance(
            source="worldpop",
            request_url="https://x.test/chn.tif",
            fetched_at=datetime(2026, 8, 14, tzinfo=UTC),
        ),
    )
