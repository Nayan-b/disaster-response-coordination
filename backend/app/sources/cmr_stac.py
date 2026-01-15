"""NASA flood imagery: CMR-STAC granule search + GIBS pre-rendered tiles.

Two complementary paths, because neither alone covers the project's needs:

* **CMR-STAC** (`LANCEMODIS` provider) searches the MODIS/VIIRS near-real-time global flood
  products. It gives precise granule references, but LANCE keeps only a short rolling NRT archive,
  so queries for historical events legitimately return zero granules.
* **GIBS** serves pre-rendered flood tiles back to 2021-01-01 with no auth. This is what makes the
  historical "replay" demo viable, and what the map UI consumes directly.

When CMR has nothing for a date range, that is reported explicitly as unavailable — the caller may
then fall back to GIBS tiles, but the gap is never papered over with a nearby granule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Sequence

from app import config
from app.fetch import fetch_json, parse_gdacs_datetime
from app.models import (
    ImageryGranule,
    ImageryResult,
    Provenance,
    SourceUnavailable,
    TileLayer,
)

SOURCE = "cmr_stac"
GIBS_SOURCE = "gibs"

# Verified flood collections under the LANCEMODIS provider. The bare `MCDWD_L3_NRT_6.1` is the
# general product; F1/F2/F3 are 1/2/3-day composites, F1C is 1-day cloud-shadow-corrected.
FLOOD_COLLECTIONS = (
    "MCDWD_L3_NRT_6.1",
    "MCDWD_L3_F1_NRT_6.1",
    "MCDWD_L3_F2_NRT_6.1",
    "MCDWD_L3_F3_NRT_6.1",
    "MCDWD_L3_F1C_NRT_6.1",
)

# Corresponding GIBS layers (MODIS and VIIRS composites over 1/2/3 days).
GIBS_FLOOD_LAYERS = (
    "MODIS_Combined_Flood_1-Day",
    "MODIS_Combined_Flood_2-Day",
    "MODIS_Combined_Flood_3-Day",
    "VIIRS_Combined_Flood_1-Day",
    "VIIRS_Combined_Flood_2-Day",
    "VIIRS_Combined_Flood_3-Day",
)

# Data-asset extensions, GeoTIFF first. The F1/F2/F3/F1C composite collections serve `.tif`; the
# bare MCDWD_L3_NRT collection serves `.hdf`, which is still a valid reference but needs extra
# tooling to read, so it is recorded and ranked second rather than preferred.
RASTER_SUFFIXES = (".tif", ".tiff", ".hdf")


def _stac_search_url() -> str:
    return f"{config.CMR_STAC_BASE}/{config.CMR_FLOOD_PROVIDER}/search"


def _to_utc(value: date | datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = parse_gdacs_datetime(value)
        if parsed is None:
            raise ValueError(f"could not parse datetime {value!r}")
        return parsed
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _select_asset_href(
    assets: dict[str, Any], suffixes: Sequence[str] = RASTER_SUFFIXES
) -> tuple[str | None, str | None]:
    """Pick the data asset by href suffix rather than by key name; returns `(href, format)`.

    Asset keys here are not stable identifiers: the bare MCDWD_L3_NRT collection keys its data asset
    by a *dynamic production timestamp* (e.g. "2026220030232"), while the composite collections use
    "061". Only `metadata` and `browse`/`thumbnail_N` are predictable. Matching on the href
    extension is the only reliable selector.

    Suffixes are tried in order, so GeoTIFF wins over HDF when a granule offers both.
    """
    for suffix in suffixes:
        for key, asset in assets.items():
            if not isinstance(asset, dict) or key.startswith("s3_"):
                # `s3_`-prefixed mirrors need AWS credentials, not just an Earthdata login.
                continue
            href = asset.get("href") or ""
            if href.lower().endswith(suffix):
                return href, suffix.lstrip(".")
    return None, None


def _parse_granule(feature: dict[str, Any]) -> ImageryGranule | None:
    granule_id = feature.get("id")
    if not granule_id:
        return None

    props = feature.get("properties") or {}
    assets = feature.get("assets") or {}

    when = props.get("datetime") or props.get("start_datetime")
    bbox_raw = feature.get("bbox")
    bbox: tuple[float, float, float, float] | None = None
    if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) >= 4:
        bbox = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )

    browse = assets.get("browse") or {}
    metadata = assets.get("metadata") or {}
    data_href, data_format = _select_asset_href(assets)

    return ImageryGranule(
        granule_id=str(granule_id),
        collection=feature.get("collection"),
        datetime_utc=parse_gdacs_datetime(when) if isinstance(when, str) else None,
        bbox=bbox,
        data_href=data_href,
        data_format=data_format,
        browse_href=browse.get("href") if isinstance(browse, dict) else None,
        metadata_href=metadata.get("href") if isinstance(metadata, dict) else None,
    )


def search_flood_imagery(
    bbox: Sequence[float],
    start: date | datetime | str,
    end: date | datetime | str,
    *,
    collection: str = config.CMR_DEFAULT_FLOOD_COLLECTION,
    limit: int = 10,
    refresh: bool = False,
) -> ImageryResult:
    """Search CMR-STAC for flood-product granules intersecting `bbox` in a date range.

    Raises `SourceUnavailable(recoverable=True)` when nothing matches — expected for dates outside
    LANCE's short NRT archive. Callers should surface that and fall back to `gibs_tile_layer()`,
    never to a granule from a different date.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must be (minx, miny, maxx, maxy), got {bbox!r}")

    start_dt, end_dt = _to_utc(start), _to_utc(end)
    if start_dt >= end_dt:
        raise ValueError(f"start {start_dt} must be before end {end_dt}")

    params = {
        "collections": collection,
        "bbox": ",".join(str(round(float(v), 4)) for v in bbox),
        "datetime": f"{_iso(start_dt)}/{_iso(end_dt)}",
        "limit": limit,
    }

    payload, provenance = fetch_json(
        SOURCE,
        _stac_search_url(),
        params,
        ttl=config.TTL_CMR_STAC,
        refresh=refresh,
    )

    if not isinstance(payload, dict) or "features" not in payload:
        raise SourceUnavailable(
            SOURCE,
            f"expected a STAC FeatureCollection, got keys "
            f"{list(payload)[:6] if isinstance(payload, dict) else type(payload).__name__}",
            provenance.request_url,
        )

    context = payload.get("context") or {}
    matched = context.get("matched")

    granules = [
        granule
        for granule in (
            _parse_granule(feature)
            for feature in payload.get("features") or []
            if isinstance(feature, dict)
        )
        if granule is not None
    ]

    if not granules:
        raise SourceUnavailable(
            SOURCE,
            f"no {collection} granules for bbox {params['bbox']} in {params['datetime']} — "
            f"the LANCE near-real-time archive is short, so historical dates return nothing. "
            f"Use GIBS tiles (available from {config.GIBS_EARLIEST_DATE}) for historical imagery.",
            provenance.request_url,
            recoverable=True,
        )

    granules.sort(key=lambda g: g.datetime_utc or datetime.min.replace(tzinfo=UTC), reverse=True)

    notes: list[str] = []
    without_data = sum(1 for g in granules if not g.data_href)
    if without_data:
        notes.append(f"{without_data}/{len(granules)} granules expose no downloadable data asset")
    if any(g.data_format == "hdf" for g in granules):
        notes.append("data assets are HDF; use an F1/F2/F3/F1C collection for GeoTIFF")
    note = "; ".join(notes) or None

    return ImageryResult(
        collection=collection,
        bbox_queried=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        start=start_dt,
        end=end_dt,
        matched=matched,
        granules=granules,
        provenance=provenance.model_copy(update={"note": note}) if note else provenance,
    )


def search_recent_flood_imagery(
    bbox: Sequence[float],
    *,
    days: int = 5,
    collection: str = config.CMR_DEFAULT_FLOOD_COLLECTION,
    limit: int = 10,
    refresh: bool = False,
) -> ImageryResult:
    """Convenience wrapper: the last `days` of flood granules over a bbox."""
    now = datetime.now(UTC)
    return search_flood_imagery(
        bbox,
        now - timedelta(days=days),
        now,
        collection=collection,
        limit=limit,
        refresh=refresh,
    )


def gibs_tile_layer(
    when: date | datetime | str | None = None,
    *,
    layer: str = config.GIBS_DEFAULT_FLOOD_LAYER,
    provenance: Provenance | None = None,
) -> TileLayer:
    """Build a GIBS WMTS tile-URL template for a flood layer on a given date.

    No network call is needed — the template is constructed from the layer's documented capabilities
    — so this always succeeds and is the reliable fallback when CMR-STAC has no granules. Provenance
    records the template itself as the reference, with the fetch time being construction time.

    IMPORTANT: the WMTS path order is `{TileMatrix}/{TileRow}/{TileCol}` — that is z/y/x, row BEFORE
    column. Leaflet's `{z}/{y}/{x}` placeholders line up as written; a naive z/x/y wiring silently
    serves transposed tiles.
    """
    if layer not in GIBS_FLOOD_LAYERS:
        raise ValueError(f"unknown GIBS flood layer {layer!r}; expected one of {GIBS_FLOOD_LAYERS}")

    target = _to_utc(when) if when is not None else datetime.now(UTC)
    day = target.date().isoformat()

    note = None
    if day < config.GIBS_EARLIEST_DATE:
        note = (
            f"requested date {day} precedes GIBS flood-layer coverage "
            f"(starts {config.GIBS_EARLIEST_DATE}); tiles will be blank"
        )

    template = (
        f"{config.GIBS_TILE_BASE}/{layer}/default/{day}/"
        f"{config.GIBS_TILE_MATRIX_SET}/{{z}}/{{y}}/{{x}}.png"
    )

    if provenance is None:
        provenance = Provenance(
            source=GIBS_SOURCE,
            request_url=template,
            fetched_at=datetime.now(UTC),
            from_cache=False,
            note=note,
        )
    elif note:
        provenance = provenance.model_copy(update={"note": note})

    return TileLayer(
        layer=layer,
        date=day,
        url_template=template,
        tile_matrix_set=config.GIBS_TILE_MATRIX_SET,
        provenance=provenance,
    )


def example_tile_url(layer_spec: TileLayer, z: int = 6, y: int = 25, x: int = 45) -> str:
    """Concrete tile URL from a template — used by tests and the CLI to prove tiles resolve."""
    return (
        layer_spec.url_template.replace("{z}", str(z)).replace("{y}", str(y)).replace("{x}", str(x))
    )
