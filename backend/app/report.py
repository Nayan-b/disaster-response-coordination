"""Assemble all three sources for one flood event into a single reviewable report.

This lives outside the CLI on purpose: Phase 2 wraps these same calls as MCP tools and Phase 3's
agents consume the result, so the assembly order and the failure handling belong here rather than in
a presentation layer.

The contract that matters: a source that fails does not fail the report. Its slot stays empty and the
reason is recorded in `EventReport.unavailable`, so a responder sees "population: unavailable
because X" instead of a confident-looking number with a hidden hole in it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

from app import config
from app.models import EventReport, FloodEvent, SourceUnavailable
from app.sources import cmr_stac, gdacs, worldpop

Reporter = Callable[[str], None]


def _noop(_message: str) -> None:
    return None


def _iso3_codes(event: FloodEvent) -> list[str]:
    """Country codes to pull population rasters for, affected-countries first."""
    codes = [c.iso3 for c in event.affected_countries if c.iso3]
    if not codes and event.iso3:
        codes = [event.iso3]
    return codes


def imagery_reference_date(event: FloodEvent) -> datetime:
    """The date to request imagery for.

    An event's `to_date` is often in the future (GDACS publishes a forecast window), and asking for
    tomorrow's imagery returns blank tiles. Clamp to today.
    """
    now = datetime.now(UTC)
    candidate = event.to_date or event.from_date or now
    return min(candidate, now)


def build_event_report(
    event_id: int,
    episode_id: int | None = None,
    *,
    days_lookback: int = 365,
    imagery_days: int = 7,
    collection: str | None = None,
    alert_levels: Sequence[str] = gdacs.ALERT_LEVELS,
    refresh: bool = False,
    progress: Reporter = _noop,
) -> EventReport:
    """Gather event, extent, imagery, tiles and population for one event.

    Only the event lookup is fatal — without it there is nothing to report on. Every other source
    degrades to an entry in `unavailable`.
    """
    progress(f"Looking up GDACS event {event_id}…")
    event = gdacs.get_event(
        event_id, days=days_lookback, alert_levels=alert_levels, refresh=refresh
    )

    episode = episode_id if episode_id is not None else event.episode_id
    report = EventReport(event=event)

    if episode is None:
        report.unavailable["extent"] = (
            "event has no episode id, which the GDACS geometry endpoint requires"
        )
        return report

    progress(f"Fetching affected-area polygon (event {event_id}, episode {episode})…")
    try:
        report.extent = gdacs.get_flood_extent(event_id, episode, refresh=refresh)
    except SourceUnavailable as exc:
        report.unavailable["extent"] = exc.reason
        # Imagery and population both need a real extent. Say so once, rather than reporting three
        # separate failures that all trace back to the same missing polygon.
        report.unavailable["imagery"] = "no flood extent available to search imagery for"
        report.unavailable["population"] = "no flood extent available to sum population over"
        return report

    reference_date = imagery_reference_date(event)

    progress("Searching CMR-STAC for flood imagery…")
    try:
        report.imagery = cmr_stac.search_flood_imagery(
            report.extent.bbox,
            reference_date - timedelta(days=imagery_days),
            reference_date,
            collection=collection or config.CMR_DEFAULT_FLOOD_COLLECTION,
            refresh=refresh,
        )
    except SourceUnavailable as exc:
        report.unavailable["imagery"] = exc.reason

    # Tiles are built offline from the layer's documented capabilities, so they are always available
    # and cover the historical range that the NRT granule archive does not.
    progress("Building GIBS tile layer…")
    report.tiles = cmr_stac.gibs_tile_layer(reference_date)

    codes = _iso3_codes(event)
    if not codes:
        report.unavailable["population"] = "event lists no affected country code"
        return report

    progress(f"Summing WorldPop population for {', '.join(codes)}…")
    try:
        report.population = worldpop.population_in_geometry(
            report.extent.geometry,
            codes,
            event_date=event.from_date,
            refresh=refresh,
        )
    except SourceUnavailable as exc:
        report.unavailable["population"] = exc.reason

    return report


def report_summary(report: EventReport) -> dict[str, Any]:
    """Flat dict of the report's headline figures, each paired with its source.

    Shaped for the Fact-Check agent in Phase 3: it needs claim -> value -> source, and anything
    absent here is something no downstream agent is permitted to assert.
    """
    summary: dict[str, Any] = {
        "event_id": report.event.event_id,
        "event_name": report.event.name,
        "alert_level": report.event.alert_level,
        "country": report.event.country,
        "sources": {name: prov.describe() for name, prov in report.provenance_records()},
        "unavailable": dict(report.unavailable),
    }
    if report.extent:
        summary["bbox"] = report.extent.bbox
    if report.population:
        summary["population"] = report.population.population
        summary["population_is_partial"] = report.population.is_partial
    if report.imagery:
        summary["granules"] = len(report.imagery.granules)
    if report.tiles:
        summary["tile_template"] = report.tiles.url_template
    return summary
