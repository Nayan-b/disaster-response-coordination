"""WorldPop — population inside a flood extent, by zonal sum over a persons-per-pixel raster.

Approach: resolve the per-country 1 km GeoTIFF via the WorldPop Hub REST API, cache it to disk, and
sum the pixels falling inside the GDACS affected-area polygon with rasterio.

Why local raster math rather than WorldPop's own stats service: the async endpoint at
`api.worldpop.org/v1/services/stats` is effectively broken — it returns a task id whose status never
leaves `"created"` (verified across separate tasks and polygon sizes) and leaks PHP warnings into the
response body. Local computation is also reproducible and works offline once cached.

Why 1 km rather than 100 m: the files are ~100x smaller (Nepal 0.9 MB vs 92 MB) and WorldPop does
not honor HTTP range requests — `Accept-Ranges: bytes` is advertised, but a ranged request returns
the full body with HTTP 200 — so the entire file must be downloaded either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom

from app import config
from app.fetch import fetch_file, fetch_json
from app.models import PopulationEstimate, Provenance, SourceUnavailable

SOURCE = "worldpop"

GEOTIFF_SUFFIXES = (".tif", ".tiff")

# UN-adjusted FIRST, deliberately. Validated against known national populations:
#
#   country   wpicuadj1km (UN-adj)   wpic1km (unconstrained)   UN figure
#   Nepal            29,136,808              40,297,824         ~29.1M
#   Pakistan        220,892,352             231,216,336         ~220M
#   China        1,439,323,776           1,444,326,144          ~1.41B
#
# The UN-adjusted product tracks the UN totals closely; the unconstrained one overstates by an
# amount that varies wildly by country (+38% for Nepal, +5% for Pakistan). Defaulting to the
# unconstrained product would inflate every affected-population figure by an unpredictable margin —
# unacceptable for a number that drives resource allocation.
UN_ADJUSTED_ALIAS = "wpicuadj1km"
DEFAULT_ALIAS_CHAIN = (UN_ADJUSTED_ALIAS, "wpic1km")

# The 100 m products are NOT in the default chain: they are 50-500 MB per country, and silently
# downloading that much on a routine query would be a surprise. Callers can opt in explicitly.
HIGH_RES_ALIASES = ("wpgp", "wpgpunadj")

# WorldPop rasters are in geographic coordinates.
EXPECTED_CRS = CRS.from_epsg(4326)


def _select_geotiff(files: Iterable[Any]) -> str | None:
    """Pick a GeoTIFF from a WorldPop record's `files` list.

    `files[0]` is not reliably a raster — Pakistan's 1 km entry is `pak_ppp_2020_1km_ASCII_XYZ.zip`.
    Selecting by index would hand a zip archive to rasterio.
    """
    for entry in files or []:
        if isinstance(entry, str) and entry.lower().endswith(GEOTIFF_SUFFIXES):
            return entry
    return None


def resolve_dataset(
    iso3: str,
    *,
    year: int = config.WORLDPOP_DEFAULT_YEAR,
    alias_chain: Sequence[str] = DEFAULT_ALIAS_CHAIN,
    refresh: bool = False,
) -> tuple[str, dict[str, Any], str, Provenance]:
    """Find a downloadable population GeoTIFF for a country and year.

    Returns `(geotiff_url, record, alias_used, provenance)`. The alias is returned so callers can
    flag when the fallback (non-UN-adjusted) product was used. Raises `SourceUnavailable` if no alias
    in the chain offers a GeoTIFF, naming the high-resolution opt-in in the message rather than
    silently escalating to a several-hundred-MB download.
    """
    iso3 = iso3.upper()
    attempts: list[str] = []
    last_provenance: Provenance | None = None

    for alias in alias_chain:
        url = f"{config.WORLDPOP_HUB_BASE}/{alias}"
        try:
            payload, provenance = fetch_json(
                SOURCE,
                url,
                {"iso3": iso3},
                ttl=config.TTL_WORLDPOP_METADATA,
                refresh=refresh,
            )
        except SourceUnavailable as exc:
            attempts.append(f"{alias}: {exc.reason}")
            continue

        last_provenance = provenance
        records = payload.get("data") if isinstance(payload, dict) else None
        if not records:
            attempts.append(f"{alias}: no records for {iso3}")
            continue

        matching = [r for r in records if str(r.get("popyear")) == str(year)]
        if not matching:
            years = sorted({str(r.get("popyear")) for r in records if r.get("popyear")})
            attempts.append(f"{alias}: no data for year {year} (has {years[:3]}...{years[-1:]})")
            continue

        for record in matching:
            geotiff = _select_geotiff(record.get("files"))
            if geotiff:
                return geotiff, record, alias, provenance
            attempts.append(
                f"{alias}: year {year} record has no GeoTIFF "
                f"(files: {[Path(str(f)).name for f in record.get('files') or []]})"
            )

    raise SourceUnavailable(
        SOURCE,
        f"no 1 km population GeoTIFF for {iso3} year {year}. Attempts: {'; '.join(attempts)}. "
        f"A higher-resolution product may exist — pass alias_chain={HIGH_RES_ALIASES} to opt into "
        f"100 m rasters (50-500 MB per country).",
        last_provenance.request_url if last_provenance else config.WORLDPOP_HUB_BASE,
        recoverable=True,
    )


def download_raster(url: str, *, refresh: bool = False, on_progress: Any | None = None) -> tuple[Path, Provenance]:
    """Download and cache a population raster. Cached permanently — these are static annual products."""
    return fetch_file(
        SOURCE,
        url,
        suffix=".tif",
        ttl=config.TTL_PERMANENT,
        refresh=refresh,
        on_progress=on_progress,
    )


def _zonal_sum(raster_path: Path, geometry: dict[str, Any], url: str) -> tuple[float, int, int]:
    """Sum persons-per-pixel values inside a geometry.

    Returns `(population, pixels_counted, nodata_pixels_inside)`. The last figure counts only pixels
    *inside* the polygon that carry no data — pixels outside the polygon are not a data gap and are
    excluded from it, so a high value genuinely means missing coverage.

    WorldPop pixels are *counts of people per pixel*, so the correct aggregation is a plain sum —
    not a mean, and not area-weighted. Averaging would understate a flood zone by orders of
    magnitude.
    """
    with rasterio.open(raster_path) as src:
        shape = geometry
        if src.crs and src.crs != EXPECTED_CRS:
            # Reproject the polygon rather than assuming a match; a CRS mismatch would place the
            # window in the wrong part of the world and return a plausible but wrong number.
            shape = transform_geom(EXPECTED_CRS.to_string(), src.crs.to_string(), geometry)

        try:
            windowed, window_transform = rio_mask(
                src, [shape], crop=True, filled=False, nodata=src.nodata
            )
        except ValueError as exc:
            # rasterio raises this when the shape does not overlap the raster at all.
            raise SourceUnavailable(
                SOURCE,
                f"flood polygon does not overlap {raster_path.name}: {exc}",
                url,
                recoverable=True,
            ) from exc

        band = windowed[0]

    values = np.ma.filled(band, np.nan).astype("float64")

    # Rasterize the polygon over the cropped window so "outside the polygon" and "inside but no
    # data" can be told apart. Relying on the masked array alone conflates the two, which would
    # report a mostly-out-of-polygon bbox as though the raster had huge coverage gaps.
    inside = ~geometry_mask(
        [shape], out_shape=band.shape, transform=window_transform, invert=False
    )

    # Values are excluded when non-finite or negative. WorldPop's nodata sentinel here is -99999.0;
    # a population count cannot be negative, so any negative is a sentinel that would otherwise
    # silently subtract from the total. (A naive sum over Nepal's raster gives -2.8e10.)
    usable = np.isfinite(values) & (values >= 0)
    if isinstance(band, np.ma.MaskedArray):
        usable &= ~np.ma.getmaskarray(band)

    valid = inside & usable
    population = float(values[valid].sum())
    return population, int(valid.sum()), int((inside & ~usable).sum())


def population_in_geometry(
    geometry: dict[str, Any],
    iso3_codes: Sequence[str],
    *,
    year: int = config.WORLDPOP_DEFAULT_YEAR,
    alias_chain: Sequence[str] = DEFAULT_ALIAS_CHAIN,
    event_date: datetime | None = None,
    refresh: bool = False,
    on_progress: Any | None = None,
) -> PopulationEstimate:
    """Estimate population inside `geometry`, summing across each affected country's raster.

    Multi-country events are handled by summing per-country rasters, since WorldPop publishes per
    country. Countries whose raster is unavailable are recorded in `iso3_missing` so the shortfall is
    visible instead of being implied complete — a partial sum presented as a total is exactly the
    kind of number that should never reach a responder unqualified.

    Raises `SourceUnavailable` only when *no* country yielded data.
    """
    if not iso3_codes:
        raise ValueError("iso3_codes must contain at least one country code")

    per_country: dict[str, float] = {}
    missing: dict[str, str] = {}
    total_pixels = 0
    total_nodata = 0
    resolutions: set[str] = set()
    urls: list[str] = []
    unadjusted: list[str] = []
    earliest_fetch: datetime | None = None
    any_from_cache = False

    for iso3 in dict.fromkeys(code.upper() for code in iso3_codes if code):
        try:
            url, record, alias_used, meta_provenance = resolve_dataset(
                iso3, year=year, alias_chain=alias_chain, refresh=refresh
            )
            raster_path, raster_provenance = download_raster(
                url, refresh=refresh, on_progress=on_progress
            )
            population, counted, skipped = _zonal_sum(raster_path, geometry, url)
        except SourceUnavailable as exc:
            missing[iso3] = exc.reason
            continue

        per_country[iso3] = population
        total_pixels += counted
        total_nodata += skipped
        resolutions.add(str(record.get("resolution") or "1km"))
        urls.append(url)
        if alias_used != UN_ADJUSTED_ALIAS:
            unadjusted.append(iso3)
        any_from_cache = any_from_cache or raster_provenance.from_cache
        stamp = raster_provenance.fetched_at
        earliest_fetch = stamp if earliest_fetch is None else min(earliest_fetch, stamp)
        del meta_provenance  # metadata lookup is incidental; the raster is the cited source

    if not per_country:
        raise SourceUnavailable(
            SOURCE,
            "no population raster available for any affected country: "
            + "; ".join(f"{k} ({v})" for k, v in missing.items()),
            config.WORLDPOP_HUB_BASE,
            recoverable=True,
        )

    notes: list[str] = []
    if event_date and event_date.year != year:
        # A responder should see when population data predates the event rather than assume currency.
        notes.append(f"population raster is {year}, event is {event_date.year}")
    if missing:
        notes.append(
            f"PARTIAL: no data for {sorted(missing)} — total understates the affected population"
        )
    if unadjusted:
        notes.append(
            f"{unadjusted} used the non-UN-adjusted product, which overstates national totals by "
            f"up to ~38% — treat these figures as an upper bound"
        )

    provenance = Provenance(
        source=SOURCE,
        request_url=urls[0] if len(urls) == 1 else f"{len(urls)} rasters: " + ", ".join(urls),
        fetched_at=earliest_fetch or datetime.now(UTC),
        from_cache=any_from_cache,
        note="; ".join(notes) or None,
    )

    return PopulationEstimate(
        population=sum(per_country.values()),
        iso3_covered=sorted(per_country),
        iso3_missing=sorted(missing),
        raster_year=year,
        resolution="/".join(sorted(resolutions)) or "1km",
        pixels_counted=total_pixels,
        nodata_pixels_skipped=total_nodata,
        per_country=per_country,
        provenance=provenance,
    )
