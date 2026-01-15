"""WorldPop dataset selection and zonal-sum correctness tests.

The zonal-sum tests are the highest-value tests in the suite: a wrong aggregation here produces a
plausible-looking population figure, which is precisely the kind of number that must never reach a
responder unchallenged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import SourceUnavailable
from app.sources import worldpop

# The synthetic raster in conftest spans lon 0-4, lat 0-4 with 10 people per valid pixel and two
# nodata pixels at opposite corners.
FULL_EXTENT = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [0.0, 0.0]]],
}
# Bottom-left quadrant: lon 0-2, lat 0-2 → 4 pixels, all valid.
QUADRANT = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
}


def test_un_adjusted_product_is_the_default():
    """The unconstrained product overstates national totals by up to ~38% (Nepal: 40.3M vs 29.1M)."""
    assert worldpop.DEFAULT_ALIAS_CHAIN[0] == worldpop.UN_ADJUSTED_ALIAS == "wpicuadj1km"


def test_high_res_products_are_not_in_the_default_chain():
    """100m rasters are 50-500MB per country and must be an explicit opt-in."""
    assert not set(worldpop.HIGH_RES_ALIASES) & set(worldpop.DEFAULT_ALIAS_CHAIN)


def test_selects_geotiff_not_zip_archive():
    """Pakistan's real 1km record lists an ASCII_XYZ zip; files[0] would hand it to rasterio."""
    chosen = worldpop._select_geotiff(
        [
            "https://data.worldpop.org/.../pak_ppp_2020_1km_ASCII_XYZ.zip",
            "https://data.worldpop.org/.../pak_ppp_2020_1km_Aggregated.tif",
        ]
    )
    assert chosen.endswith(".tif")


def test_no_geotiff_returns_none():
    assert worldpop._select_geotiff(["https://example.test/data.zip"]) is None
    assert worldpop._select_geotiff([]) is None


def test_resolve_scans_all_records_for_the_year(fixture_fetch):
    """Two 2020 records exist; only the second has a raster."""
    fixture_fetch(worldpop, "worldpop_records.json")
    url, record, alias, _prov = worldpop.resolve_dataset("PAK", year=2020, alias_chain=("wpicuadj1km",))

    assert url.endswith("pak_ppp_2020_1km_Aggregated.tif")
    assert record["popyear"] == "2020"
    assert alias == "wpicuadj1km"


def test_resolve_reports_available_years_when_year_missing(fixture_fetch):
    fixture_fetch(worldpop, "worldpop_records.json")

    with pytest.raises(SourceUnavailable) as excinfo:
        worldpop.resolve_dataset("PAK", year=1999, alias_chain=("wpicuadj1km",))

    assert "no data for year 1999" in excinfo.value.reason
    # The message names the opt-in instead of silently escalating to a huge download.
    assert "100 m" in excinfo.value.reason or "wpgp" in excinfo.value.reason
    assert excinfo.value.recoverable is True


def test_resolve_falls_through_alias_chain(fixture_fetch):
    """If the UN-adjusted alias has no raster, the next alias is tried and reported."""
    calls = fixture_fetch(worldpop, payload={"data": []})
    with pytest.raises(SourceUnavailable):
        worldpop.resolve_dataset("NPL", alias_chain=("wpicuadj1km", "wpic1km"))

    assert [url.rsplit("/", 1)[-1] for url in (c[0] for c in calls)] == ["wpicuadj1km", "wpic1km"]


# --- zonal sum --------------------------------------------------------------


def test_sums_rather_than_averages(population_raster):
    """Pixels are counts of people, so aggregation is a sum. A mean would give 10, not 140."""
    population, counted, nodata = worldpop._zonal_sum(population_raster, FULL_EXTENT, "test://")

    assert population == 140.0  # 14 valid pixels x 10 people
    assert counted == 14
    assert nodata == 2


def test_nodata_sentinel_is_excluded(population_raster):
    """A naive sum including -99999.0 would go negative — Nepal's real raster gives -2.8e10."""
    population, _counted, _nodata = worldpop._zonal_sum(population_raster, FULL_EXTENT, "test://")
    assert population > 0
    assert population == 140.0


def test_only_pixels_inside_the_polygon_count(population_raster):
    """The quadrant holds 4 valid pixels, so 40 people — not the raster's full 140."""
    population, counted, nodata = worldpop._zonal_sum(population_raster, QUADRANT, "test://")

    assert counted == 4
    assert population == 40.0
    # Both nodata pixels sit outside this quadrant, so there is no in-polygon gap to report.
    assert nodata == 0


def test_nodata_count_excludes_pixels_outside_polygon(population_raster):
    """Counting out-of-polygon pixels as gaps once reported ~50% of a real event as missing data."""
    _pop, counted, nodata = worldpop._zonal_sum(population_raster, QUADRANT, "test://")
    assert counted + nodata == 4  # the polygon's own pixels only, not the 4x4 window


def test_non_overlapping_polygon_raises(population_raster):
    """A polygon off the raster must fail loudly rather than return 0 people."""
    elsewhere = {
        "type": "Polygon",
        "coordinates": [[[-40.0, -20.0], [-39.0, -20.0], [-39.0, -19.0], [-40.0, -19.0], [-40.0, -20.0]]],
    }
    with pytest.raises(SourceUnavailable) as excinfo:
        worldpop._zonal_sum(population_raster, elsewhere, "test://")

    assert "does not overlap" in excinfo.value.reason
    assert excinfo.value.recoverable is True


# --- multi-country aggregation ---------------------------------------------


def _patch_single_country(monkeypatch, population_raster, available: set[str]):
    """Stub dataset resolution/download so only `available` countries have a raster."""

    def fake_resolve(iso3, *, year=2020, alias_chain=(), refresh=False):
        from app.models import Provenance

        if iso3 not in available:
            raise SourceUnavailable("worldpop", f"no raster for {iso3}", "test://", recoverable=True)
        provenance = Provenance(
            source="worldpop",
            request_url=f"test://{iso3}",
            fetched_at=datetime(2026, 8, 14, tzinfo=UTC),
        )
        return f"test://{iso3}.tif", {"resolution": "1km"}, worldpop.UN_ADJUSTED_ALIAS, provenance

    def fake_download(url, *, refresh=False, on_progress=None):
        from app.models import Provenance

        return population_raster, Provenance(
            source="worldpop",
            request_url=url,
            fetched_at=datetime(2026, 8, 14, tzinfo=UTC),
        )

    monkeypatch.setattr(worldpop, "resolve_dataset", fake_resolve)
    monkeypatch.setattr(worldpop, "download_raster", fake_download)


def test_sums_across_affected_countries(monkeypatch, population_raster):
    _patch_single_country(monkeypatch, population_raster, {"SSD", "ETH"})
    estimate = worldpop.population_in_geometry(QUADRANT, ["SSD", "ETH"])

    assert estimate.population == 80.0  # 40 per country
    assert estimate.iso3_covered == ["ETH", "SSD"]
    assert estimate.is_partial is False


def test_partial_coverage_is_flagged_loudly(monkeypatch, population_raster):
    """A total missing one country must never be presented as complete."""
    _patch_single_country(monkeypatch, population_raster, {"SSD"})
    estimate = worldpop.population_in_geometry(QUADRANT, ["SSD", "ETH"])

    assert estimate.is_partial is True
    assert estimate.iso3_missing == ["ETH"]
    assert "PARTIAL" in estimate.provenance.note
    assert "understates" in estimate.provenance.note


def test_all_countries_missing_raises(monkeypatch, population_raster):
    _patch_single_country(monkeypatch, population_raster, set())
    with pytest.raises(SourceUnavailable, match="no population raster available"):
        worldpop.population_in_geometry(QUADRANT, ["SSD", "ETH"])


def test_stale_raster_year_is_noted(monkeypatch, population_raster):
    """A responder should see that population data predates the event."""
    _patch_single_country(monkeypatch, population_raster, {"SSD"})
    estimate = worldpop.population_in_geometry(
        QUADRANT, ["SSD"], year=2020, event_date=datetime(2026, 7, 1, tzinfo=UTC)
    )

    assert "raster is 2020, event is 2026" in estimate.provenance.note


def test_duplicate_country_codes_counted_once(monkeypatch, population_raster):
    _patch_single_country(monkeypatch, population_raster, {"SSD"})
    estimate = worldpop.population_in_geometry(QUADRANT, ["SSD", "ssd", "SSD"])

    assert estimate.population == 40.0
    assert estimate.iso3_covered == ["SSD"]


def test_requires_at_least_one_country():
    with pytest.raises(ValueError, match="at least one country"):
        worldpop.population_in_geometry(QUADRANT, [])
