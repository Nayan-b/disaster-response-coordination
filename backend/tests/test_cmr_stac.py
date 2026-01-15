"""CMR-STAC granule parsing and GIBS tile-template tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import config
from app.models import SourceUnavailable
from app.sources import cmr_stac


def test_selects_tif_asset_despite_unstable_key(fixture_fetch):
    """The data asset is keyed '061' here and by a timestamp elsewhere — match on the href."""
    fixture_fetch(cmr_stac, "stac_search_tif.json")
    result = cmr_stac.search_flood_imagery([100, 30, 110, 40], "2026-08-10", "2026-08-14")

    granule = result.granules[0]
    assert granule.data_format == "tif"
    assert granule.data_href.endswith(".tif")
    assert granule.data_href.startswith("https://")


def test_skips_s3_mirror_assets(fixture_fetch):
    """s3_ assets need AWS credentials rather than an Earthdata login."""
    fixture_fetch(cmr_stac, "stac_search_tif.json")
    result = cmr_stac.search_flood_imagery([100, 30, 110, 40], "2026-08-10", "2026-08-14")

    assert not result.granules[0].data_href.startswith("s3://")


def test_handles_dynamic_timestamp_key_and_flags_hdf(fixture_fetch):
    """The base collection keys its asset by production timestamp and serves HDF."""
    fixture_fetch(cmr_stac, "stac_search_hdf.json")
    result = cmr_stac.search_flood_imagery(
        [100, 30, 110, 40], "2026-08-05", "2026-08-10", collection="MCDWD_L3_NRT_6.1"
    )

    granule = result.granules[0]
    assert granule.data_format == "hdf"
    assert granule.data_href.endswith(".hdf")
    assert granule.browse_href is not None
    # The caveat must be visible, since HDF needs extra tooling to read.
    assert "HDF" in (result.provenance.note or "")


def test_prefers_geotiff_over_hdf():
    """When a granule offers both, GeoTIFF wins — it is directly readable by rasterio."""
    href, fmt = cmr_stac._select_asset_href(
        {
            "aaa": {"href": "https://example.test/data.hdf"},
            "zzz": {"href": "https://example.test/data.tif"},
        }
    )
    assert fmt == "tif" and href.endswith(".tif")


def test_reports_missing_data_asset():
    href, fmt = cmr_stac._select_asset_href({"metadata": {"href": "https://example.test/m.xml"}})
    assert href is None and fmt is None


def test_empty_result_is_recoverable_unavailable(fixture_fetch):
    """Historical dates legitimately return nothing; it must not be backfilled."""
    fixture_fetch(cmr_stac, "stac_search_empty.json")

    with pytest.raises(SourceUnavailable) as excinfo:
        cmr_stac.search_flood_imagery([66, 25, 70, 29], "2022-08-20", "2022-08-31")

    assert excinfo.value.recoverable is True
    # The message must point at the alternative rather than leaving a dead end.
    assert "GIBS" in excinfo.value.reason
    assert config.GIBS_EARLIEST_DATE in excinfo.value.reason


def test_context_matched_is_surfaced(fixture_fetch):
    fixture_fetch(cmr_stac, "stac_search_tif.json")
    result = cmr_stac.search_flood_imagery([100, 30, 110, 40], "2026-08-10", "2026-08-14")

    # 6 matched vs 1 returned — the caller can tell the result was truncated by `limit`.
    assert result.matched == 6
    assert len(result.granules) == 1


def test_coarse_granule_bbox_is_preserved(fixture_fetch):
    """Granule bboxes are 10x10-degree product tiles; keeping them lets callers see the mismatch."""
    fixture_fetch(cmr_stac, "stac_search_tif.json")
    result = cmr_stac.search_flood_imagery([102.6, 34.9, 103.8, 36.2], "2026-08-10", "2026-08-14")

    assert result.granules[0].bbox == (100.0, 30.0, 110.0, 40.0)
    assert result.bbox_queried == (102.6, 34.9, 103.8, 36.2)


def test_requires_download_auth_is_declared(fixture_fetch):
    fixture_fetch(cmr_stac, "stac_search_tif.json")
    result = cmr_stac.search_flood_imagery([100, 30, 110, 40], "2026-08-10", "2026-08-14")
    assert result.requires_auth_to_download is True


def test_rejects_bad_bbox_and_date_order(fixture_fetch):
    fixture_fetch(cmr_stac, "stac_search_tif.json")

    with pytest.raises(ValueError, match="bbox must be"):
        cmr_stac.search_flood_imagery([1, 2, 3], "2026-08-01", "2026-08-02")
    with pytest.raises(ValueError, match="must be before"):
        cmr_stac.search_flood_imagery([1, 2, 3, 4], "2026-08-05", "2026-08-01")


def test_search_params_are_well_formed(fixture_fetch):
    calls = fixture_fetch(cmr_stac, "stac_search_tif.json")
    cmr_stac.search_flood_imagery([100.0, 30.0, 110.0, 40.0], "2026-08-10", "2026-08-14")

    url, params = calls[0]
    assert url.endswith(f"/{config.CMR_FLOOD_PROVIDER}/search")
    assert params["bbox"] == "100.0,30.0,110.0,40.0"
    assert params["datetime"] == "2026-08-10T00:00:00Z/2026-08-14T00:00:00Z"


# --- GIBS -------------------------------------------------------------------


def test_gibs_template_uses_row_before_column():
    """WMTS order is {TileMatrix}/{TileRow}/{TileCol} — z/y/x. A z/x/y wiring serves transposed tiles."""
    layer = cmr_stac.gibs_tile_layer("2026-08-14")

    assert layer.url_template.endswith("/{z}/{y}/{x}.png")
    assert layer.tile_matrix_set == config.GIBS_TILE_MATRIX_SET
    assert f"/{config.GIBS_DEFAULT_FLOOD_LAYER}/default/2026-08-14/" in layer.url_template


def test_gibs_needs_no_network_so_always_available():
    """The fallback must not depend on a call that could itself fail."""
    layer = cmr_stac.gibs_tile_layer(datetime(2022, 8, 28, tzinfo=UTC))
    assert layer.date == "2022-08-28"
    assert layer.provenance.source == "gibs"
    assert layer.provenance.note is None


def test_gibs_flags_dates_before_coverage():
    """Pre-2021 dates return blank tiles; the caveat must travel with the layer."""
    layer = cmr_stac.gibs_tile_layer("2019-06-01")
    assert layer.provenance.note is not None
    assert "precedes GIBS" in layer.provenance.note


def test_gibs_rejects_unknown_layer():
    with pytest.raises(ValueError, match="unknown GIBS flood layer"):
        cmr_stac.gibs_tile_layer("2026-08-14", layer="MODIS_Not_A_Real_Layer")


def test_example_tile_url_substitutes_all_placeholders():
    url = cmr_stac.example_tile_url(cmr_stac.gibs_tile_layer("2026-08-14"), 6, 25, 45)
    assert url.endswith("/6/25/45.png")
    assert "{" not in url
