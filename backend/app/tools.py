"""Tool registry — the single definition of every capability the agents can invoke.

This is the seam between the Phase 1 data layer and everything above it. The same specs are
exposed two ways with no duplicated logic:

* `app/mcp_server.py` serves them over MCP (Phase 2), for external MCP clients.
* `app/agents/` binds the same callables directly (Phase 3), avoiding a network hop for the
  in-process graph.

Two design rules shape the tool surface:

1. **Tools are addressed by event id, never by geometry.** A GDACS affected-area polygon is ~880 KB
   of coordinates. Passing it through an LLM would blow the context window, cost a fortune, and —
   worst of all — invite the model to *fabricate* or truncate coordinates. So `estimate_population`
   takes `event_id`/`episode_id` and resolves the geometry server-side. The model names the event; it
   never handles or invents spatial data.

2. **Every call is logged as a `ToolCall` with its provenance.** The Fact-Check agent verifies the
   Allocation agent's claims against these records. A number that cannot be traced to a logged call
   is, by definition, unverifiable — which is the whole point of the guardrail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

from app import config
from app.models import Provenance, SourceUnavailable, ToolCall
from app.sources import cmr_stac, gdacs, worldpop

# ---------------------------------------------------------------------------
# Tool result envelope
# ---------------------------------------------------------------------------


def _ok(data: dict[str, Any], provenance: Provenance | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, **data}
    if provenance is not None:
        payload["source"] = provenance.describe()
        payload["fetched_at"] = provenance.fetched_at.isoformat()
        if provenance.note:
            payload["caveat"] = provenance.note
    return payload


def _unavailable(reason: str, *, recoverable: bool = False) -> dict[str, Any]:
    """The shape every failure takes.

    Returned rather than raised so a tool-calling model sees a normal result it can reason about and
    report honestly. `ok: False` with a reason is the contract: an agent that reads this and then
    states a number anyway is making it up, and the Fact-Check agent will catch it.
    """
    return {
        "ok": False,
        "unavailable": True,
        "reason": reason,
        "recoverable": recoverable,
    }


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., dict[str, Any]]


@dataclass
class ToolRunner:
    """Invokes tools and keeps an append-only log of every call for later verification."""

    calls: list[ToolCall] = field(default_factory=list)

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = REGISTRY.get(name)
        if spec is None:
            return _unavailable(f"unknown tool {name!r}; available: {sorted(REGISTRY)}")

        started = datetime.now(UTC)
        try:
            result = spec.fn(**arguments)
        except SourceUnavailable as exc:
            result = _unavailable(exc.reason, recoverable=exc.recoverable)
        except TypeError as exc:
            # Bad arguments from a model — report rather than crash the graph.
            result = _unavailable(f"invalid arguments for {name}: {exc}")

        self.calls.append(
            ToolCall(
                call_id=f"tc_{len(self.calls) + 1}",
                tool=name,
                arguments=dict(arguments),
                result=result,
                called_at=started,
                ok=bool(result.get("ok")),
            )
        )
        return result

    def facts(self) -> dict[str, ToolCall]:
        """Successful calls only, keyed by call id — the evidence set for fact-checking."""
        return {call.call_id: call for call in self.calls if call.ok}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _event_summary(event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "episode_id": event.episode_id,
        "name": event.name,
        "country": event.country,
        "iso3_codes": [c.iso3 for c in event.affected_countries if c.iso3]
        or ([event.iso3] if event.iso3 else []),
        "alert_level": event.alert_level,
        "episode_alert_level": event.episode_alert_level,
        "from_date": event.from_date.date().isoformat() if event.from_date else None,
        "to_date": event.to_date.date().isoformat() if event.to_date else None,
        # Surfaced with its meaning attached so a model cannot read 0.0 as "no flood".
        "severity": event.severity_text,
        "severity_note": "GDACS leaves magnitude at 0 when unpopulated; 0 does not mean no flood",
        "report_url": event.report_url,
    }


def _urgency_key(event) -> tuple[int, float]:
    """Sort key: most severe alert level first, then the most recent event window.

    This exists because the result cap truncates the *tail* of this ordering. GDACS does not return
    events in severity order, so capping the raw list could drop a Red event and keep an Orange one
    that happened to be listed first.
    """
    level = event.alert_level or ""
    rank = gdacs.ALERT_LEVELS.index(level) + 1 if level in gdacs.ALERT_LEVELS else 0
    return (rank, event.to_date.timestamp() if event.to_date else 0.0)


def tool_list_flood_events(
    days: int = config.EVENTS_DEFAULT_LOOKBACK_DAYS,
    alert_levels: Sequence[str] | None = None,
    max_events: int = config.EVENTS_MAX_RESULTS,
    refresh: bool = False,
) -> dict[str, Any]:
    levels = tuple(alert_levels) if alert_levels else ("Orange", "Red")
    events = gdacs.list_flood_events(days=days, alert_levels=levels, refresh=refresh)
    if not events:
        return _unavailable(
            f"GDACS returned no flood events at level(s) {list(levels)} in the last {days} days. "
            "This is a real result, not a failure.",
            recoverable=True,
        )

    ranked = sorted(events, key=_urgency_key, reverse=True)
    returned = ranked[:max_events] if max_events and max_events > 0 else ranked

    payload: dict[str, Any] = {
        "event_count": len(returned),
        "total_matched": len(events),
        "events": [_event_summary(e) for e in returned],
    }
    # Never truncate silently. A caller looking at ten events must be able to tell whether that was
    # all of them or the top of a longer list — otherwise a capped result reads as full coverage.
    if len(returned) < len(events):
        payload["truncated"] = True
        payload["max_events"] = max_events
        payload["note"] = (
            f"{len(events)} events matched; returning the {len(returned)} most urgent, ranked by "
            "alert level then recency. Raise max_events to see the rest."
        )
    return _ok(payload, events[0].provenance)


def tool_get_flood_extent(
    event_id: int, episode_id: int | None = None
) -> dict[str, Any]:
    """Affected-area extent for an event. Returns the bbox and size, never the raw polygon.

    The polygon itself stays server-side (it is ~880 KB); the model gets a compact summary.
    """
    episode = (
        episode_id if episode_id is not None else gdacs.get_event(event_id).episode_id
    )
    if episode is None:
        return _unavailable(
            f"event {event_id} has no episode id, which GDACS geometry requires"
        )

    extent = gdacs.get_flood_extent(event_id, episode)
    return _ok(
        {
            "event_id": event_id,
            "episode_id": episode,
            "geometry_type": extent.geometry["type"],
            "bbox": list(extent.bbox),
            "area_sq_deg": round(extent.area_sq_deg, 4),
            "polygon_label": extent.polygon_label,
            "note": "bbox is derived from the affected-area polygon, not the event centroid",
        },
        extent.provenance,
    )


def tool_estimate_population(
    event_id: int,
    episode_id: int | None = None,
    year: int = config.WORLDPOP_DEFAULT_YEAR,
) -> dict[str, Any]:
    """Population inside an event's affected-area polygon, summed from WorldPop.

    Resolves the geometry internally — the model never supplies coordinates.
    """
    event = gdacs.get_event(event_id)
    episode = episode_id if episode_id is not None else event.episode_id
    if episode is None:
        return _unavailable(f"event {event_id} has no episode id")

    extent = gdacs.get_flood_extent(event_id, episode)
    codes = [c.iso3 for c in event.affected_countries if c.iso3] or (
        [event.iso3] if event.iso3 else []
    )
    if not codes:
        return _unavailable(f"event {event_id} lists no affected country code")

    estimate = worldpop.population_in_geometry(
        extent.geometry, codes, year=year, event_date=event.from_date
    )
    return _ok(
        {
            "population": round(estimate.population),
            "countries_covered": estimate.iso3_covered,
            "countries_missing": estimate.iso3_missing,
            "is_partial": estimate.is_partial,
            "raster_year": estimate.raster_year,
            "resolution": estimate.resolution,
            "pixels_in_polygon": estimate.pixels_counted,
            "nodata_pixels_in_polygon": estimate.nodata_pixels_skipped,
            "per_country": {k: round(v) for k, v in estimate.per_country.items()},
            "method": "sum of WorldPop UN-adjusted persons-per-pixel inside the flood polygon",
        },
        estimate.provenance,
    )


def tool_search_flood_imagery(
    event_id: int,
    episode_id: int | None = None,
    days: int = 7,
    collection: str = config.CMR_DEFAULT_FLOOD_COLLECTION,
) -> dict[str, Any]:
    """Search NASA CMR-STAC for flood granules covering an event's extent."""
    event = gdacs.get_event(event_id)
    episode = episode_id if episode_id is not None else event.episode_id
    if episode is None:
        return _unavailable(f"event {event_id} has no episode id")

    extent = gdacs.get_flood_extent(event_id, episode)
    from app.report import imagery_reference_date

    reference = imagery_reference_date(event)
    result = cmr_stac.search_flood_imagery(
        extent.bbox, reference - timedelta(days=days), reference, collection=collection
    )
    return _ok(
        {
            "collection": result.collection,
            "matched": result.matched,
            "granule_count": len(result.granules),
            "granules": [
                {
                    "granule_id": g.granule_id,
                    "date": (
                        g.datetime_utc.date().isoformat() if g.datetime_utc else None
                    ),
                    "format": g.data_format,
                    "href": g.data_href,
                }
                for g in result.granules[:5]
            ],
            "requires_earthdata_login_to_download": True,
        },
        result.provenance,
    )


def tool_get_tile_layer(
    event_id: int,
    episode_id: int | None = None,
    layer: str = config.GIBS_DEFAULT_FLOOD_LAYER,
) -> dict[str, Any]:
    """Build a NASA GIBS flood tile layer for the map, dated to the event window.

    Always succeeds — the template is constructed from documented capabilities with no network
    call, which is why it is the fallback when CMR-STAC has no granules.
    """
    event = gdacs.get_event(event_id)
    from app.report import imagery_reference_date

    tiles = cmr_stac.gibs_tile_layer(imagery_reference_date(event), layer=layer)
    return _ok(
        {
            "layer": tiles.layer,
            "date": tiles.date,
            "url_template": tiles.url_template,
            "tile_matrix_set": tiles.tile_matrix_set,
            "max_zoom": tiles.max_zoom,
            "attribution": tiles.attribution,
        },
        tiles.provenance,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_EVENT_ARGS = {
    "event_id": {"type": "integer", "description": "GDACS event id."},
    "episode_id": {
        "type": "integer",
        "description": "GDACS episode id. Omit to use the event's current episode.",
    },
}

REGISTRY: dict[str, ToolSpec] = {
    "list_flood_events": ToolSpec(
        name="list_flood_events",
        description=(
            "List active GDACS flood events with their alert level, country, and date window, "
            "most urgent first. Call this first to discover which events exist. Returns at most "
            "`max_events` events and reports `total_matched` so a capped list is never mistaken "
            "for a complete one. Returns a real empty result when no events match — that is data, "
            "not an error."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Lookback window in days "
                        f"(default {config.EVENTS_DEFAULT_LOOKBACK_DAYS})."
                    ),
                },
                "alert_levels": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(gdacs.ALERT_LEVELS)},
                    "description": "Filter on the event's overall alert level. Default Orange+Red.",
                },
                "max_events": {
                    "type": "integer",
                    "description": (
                        "Cap on how many events to return, most urgent first "
                        f"(default {config.EVENTS_MAX_RESULTS}; 0 means no cap). A truncated "
                        "result sets `truncated` and reports `total_matched`."
                    ),
                },
            },
        },
        fn=tool_list_flood_events,
    ),
    "get_flood_extent": ToolSpec(
        name="get_flood_extent",
        description=(
            "Get the affected-area extent (bounding box and size) for one flood event. The bbox is "
            "derived from the real affected-area polygon; the event list's own bbox is a zero-area "
            "centroid and must never be used. Returns unavailable when GDACS published only a "
            "centroid for the event."
        ),
        input_schema={
            "type": "object",
            "properties": dict(_EVENT_ARGS),
            "required": ["event_id"],
        },
        fn=tool_get_flood_extent,
    ),
    "estimate_population": ToolSpec(
        name="estimate_population",
        description=(
            "Estimate how many people are inside a flood event's affected-area polygon, by summing "
            "WorldPop UN-adjusted population raster values. Resolves the geometry itself — do not "
            "pass coordinates. Reports countries_missing when a country's raster is unavailable, in "
            "which case the total understates reality and must be described as partial."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_EVENT_ARGS,
                "year": {
                    "type": "integer",
                    "description": "WorldPop raster year (default 2020).",
                },
            },
            "required": ["event_id"],
        },
        fn=tool_estimate_population,
    ),
    "search_flood_imagery": ToolSpec(
        name="search_flood_imagery",
        description=(
            "Search NASA CMR-STAC for MODIS/VIIRS flood-product granules over an event's extent. "
            "The near-real-time archive is only days deep, so historical events legitimately return "
            "unavailable — use get_tile_layer for those instead of reporting no imagery exists."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_EVENT_ARGS,
                "days": {
                    "type": "integer",
                    "description": "Search window in days (default 7).",
                },
            },
            "required": ["event_id"],
        },
        fn=tool_search_flood_imagery,
    ),
    "get_tile_layer": ToolSpec(
        name="get_tile_layer",
        description=(
            "Get a NASA GIBS flood-imagery tile layer URL template for the map, dated to the "
            "event's window. Needs no network call and covers 2021 onward, so it always succeeds."
        ),
        input_schema={
            "type": "object",
            "properties": dict(_EVENT_ARGS),
            "required": ["event_id"],
        },
        fn=tool_get_tile_layer,
    ),
}


def anthropic_tool_schemas() -> list[dict[str, Any]]:
    """Tool definitions in Anthropic Messages API shape (also what LangChain binds)."""
    return [
        {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in REGISTRY.values()
    ]
