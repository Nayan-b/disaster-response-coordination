"""GDACS — Global Disaster Alert and Coordination System (UN OCHA + EU JRC).

Free, no API key. Two endpoints are used:

* `geteventlist/SEARCH` — active/historical flood events with alert levels.
* `polygons/getgeometry` — the affected-area polygon for one event episode.

The second one matters more than the brief assumed. The event list's `bbox` is, for most events, a
zero-area centroid (e.g. `[-117.02, 34.89, -117.02, 34.89]`), so any population figure computed
over it would be meaningless. The geometry endpoint returns a real `Poly_Affected` MultiPolygon, so
the bbox is *derived* from the true extent instead of guessed from a point.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Sequence

from shapely.geometry import shape

from app import config
from app.fetch import fetch_json, parse_gdacs_datetime
from app.models import (
    AffectedCountry,
    FloodEvent,
    FloodExtent,
    Provenance,
    SourceUnavailable,
)

SOURCE = "gdacs"

# GDACS alert levels, least to most severe.
ALERT_LEVELS = ("Green", "Orange", "Red")

# The affected-area polygon. GDACS also returns "Point_Centroid" (a point, useless for area math)
# and "Poly_Global" (a coarse global-context polygon, far larger than the real event).
AFFECTED_AREA_CLASS = "Poly_Affected"


def _as_date_string(value: date | datetime | str) -> str:
    """GDACS expects `YYYY-MM-DD` for its date-range parameters."""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _first_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_event(feature: dict[str, Any], provenance: Provenance) -> FloodEvent | None:
    """Normalize one GDACS GeoJSON feature. Returns None if it has no usable event id."""
    props = feature.get("properties") or {}
    event_id = props.get("eventid")
    if event_id is None:
        return None

    severity = props.get("severitydata") or {}
    urls = props.get("url") or {}

    centroid: tuple[float, float] | None = None
    geometry = feature.get("geometry") or {}
    if geometry.get("type") == "Point":
        coords = geometry.get("coordinates") or []
        if len(coords) >= 2:
            centroid = (float(coords[0]), float(coords[1]))

    countries = [
        AffectedCountry(
            iso2=entry.get("iso2"),
            iso3=entry.get("iso3"),
            name=entry.get("countryname"),
        )
        for entry in (props.get("affectedcountries") or [])
        if isinstance(entry, dict)
    ]

    # GDACS returns these as the strings "true"/"false", not JSON booleans.
    def _flag(key: str) -> bool | None:
        raw = props.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() == "true"
        return None

    return FloodEvent(
        event_id=int(event_id),
        episode_id=props.get("episodeid"),
        event_type=props.get("eventtype") or "FL",
        name=props.get("name") or props.get("eventname") or f"Flood event {event_id}",
        country=props.get("country"),
        iso3=props.get("iso3"),
        affected_countries=countries,
        # Overall and episode alert levels can disagree (an event may be overall Orange while its
        # latest episode is Green). Both are kept; collapsing them would misreport severity.
        alert_level=props.get("alertlevel"),
        alert_score=_first_float(props.get("alertscore")),
        episode_alert_level=props.get("episodealertlevel"),
        episode_alert_score=_first_float(props.get("episodealertscore")),
        from_date=parse_gdacs_datetime(props.get("fromdate")),
        to_date=parse_gdacs_datetime(props.get("todate")),
        date_modified=parse_gdacs_datetime(props.get("datemodified")),
        # NOTE: severity 0.0 with text "Magnitude 0" means GDACS did not populate the magnitude
        # field — it does NOT mean "no flood". Do not treat it as an absence of severity.
        severity_value=_first_float(severity.get("severity")),
        severity_text=severity.get("severitytext"),
        severity_unit=severity.get("severityunit") or None,
        centroid=centroid,
        is_current=_flag("iscurrent"),
        is_temporary=_flag("istemporary"),
        source_model=props.get("source") or None,
        report_url=urls.get("report"),
        geometry_url=urls.get("geometry"),
        details_url=urls.get("details"),
        provenance=provenance,
    )


def list_flood_events(
    from_date: date | datetime | str | None = None,
    to_date: date | datetime | str | None = None,
    alert_levels: Sequence[str] = ("Orange", "Red"),
    *,
    days: int | None = None,
    refresh: bool = False,
) -> list[FloodEvent]:
    """Query GDACS for flood events in a date range.

    Pass either an explicit range or `days` (a lookback window from today). `alert_levels` filters on
    the event's *overall* alert level and is sent semicolon-separated, as GDACS expects.

    Raises `SourceUnavailable` if the request fails or the response is not a FeatureCollection.
    """
    if days is not None and from_date is None:
        today = datetime.now(UTC).date()
        from_date = today - timedelta(days=days)
        to_date = to_date or today
    if from_date is None or to_date is None:
        raise ValueError("provide either from_date/to_date or days")

    invalid = [level for level in alert_levels if level not in ALERT_LEVELS]
    if invalid:
        raise ValueError(f"unknown alert level(s) {invalid}; expected any of {ALERT_LEVELS}")

    params = {
        "eventlist": "FL",
        "fromdate": _as_date_string(from_date),
        "todate": _as_date_string(to_date),
    }
    if alert_levels:
        params["alertlevel"] = ";".join(alert_levels)

    payload, provenance = fetch_json(
        SOURCE,
        config.GDACS_EVENTLIST_URL,
        params,
        ttl=config.TTL_GDACS_EVENTS,
        refresh=refresh,
    )

    if not isinstance(payload, dict) or "features" not in payload:
        raise SourceUnavailable(
            SOURCE,
            f"expected a GeoJSON FeatureCollection, got {type(payload).__name__} "
            f"with keys {list(payload)[:6] if isinstance(payload, dict) else 'n/a'}",
            provenance.request_url,
        )

    events: list[FloodEvent] = []
    for feature in payload.get("features") or []:
        if not isinstance(feature, dict):
            continue
        event = _parse_event(feature, provenance)
        if event is not None:
            events.append(event)

    # Most recently modified first — a responder cares about what just changed.
    events.sort(key=lambda e: e.date_modified or datetime.min.replace(tzinfo=UTC), reverse=True)
    return events


def get_event(
    event_id: int,
    *,
    days: int = 365,
    alert_levels: Sequence[str] = ALERT_LEVELS,
    refresh: bool = False,
) -> FloodEvent:
    """Look up a single event by id, searching back `days` across all alert levels."""
    events = list_flood_events(days=days, alert_levels=alert_levels, refresh=refresh)
    for event in events:
        if event.event_id == event_id:
            return event
    raise SourceUnavailable(
        SOURCE,
        f"event {event_id} not found in the last {days} days "
        f"(searched {len(events)} events at levels {list(alert_levels)})",
        config.GDACS_EVENTLIST_URL,
        recoverable=True,
    )


def get_flood_extent(
    event_id: int,
    episode_id: int,
    *,
    refresh: bool = False,
) -> FloodExtent:
    """Fetch the affected-area polygon for an event episode and derive its bbox.

    Raises `SourceUnavailable` (recoverable) when GDACS has only a centroid for the event, rather
    than silently substituting a buffered point — an invented extent would corrupt every population
    figure downstream.
    """
    params = {
        "eventtype": "FL",
        "eventid": event_id,
        "episodeid": episode_id,
    }
    payload, provenance = fetch_json(
        SOURCE,
        config.GDACS_GEOMETRY_URL,
        params,
        ttl=config.TTL_GDACS_GEOMETRY,
        refresh=refresh,
    )

    if not isinstance(payload, dict) or not payload.get("features"):
        raise SourceUnavailable(
            SOURCE,
            f"no geometry features returned for event {event_id} episode {episode_id}",
            provenance.request_url,
            recoverable=True,
        )

    features = [f for f in payload["features"] if isinstance(f, dict)]
    affected = _select_affected_feature(features)
    if affected is None:
        available = sorted(
            {
                (f.get("properties") or {}).get("Class") or (f.get("geometry") or {}).get("type", "?")
                for f in features
            }
        )
        raise SourceUnavailable(
            SOURCE,
            f"event {event_id} episode {episode_id} has no '{AFFECTED_AREA_CLASS}' polygon "
            f"(only {available}) — cannot derive a flood extent",
            provenance.request_url,
            recoverable=True,
        )

    geometry = affected.get("geometry") or {}
    try:
        polygon = shape(geometry)
    except Exception as exc:  # shapely raises assorted types for malformed GeoJSON
        raise SourceUnavailable(
            SOURCE,
            f"affected-area geometry could not be parsed: {type(exc).__name__}: {exc}",
            provenance.request_url,
        ) from exc

    if polygon.is_empty:
        raise SourceUnavailable(
            SOURCE,
            f"affected-area polygon for event {event_id} episode {episode_id} is empty",
            provenance.request_url,
            recoverable=True,
        )

    minx, miny, maxx, maxy = polygon.bounds
    if minx == maxx or miny == maxy:
        raise SourceUnavailable(
            SOURCE,
            f"affected-area polygon has zero-area bounds {polygon.bounds} — unusable as an extent",
            provenance.request_url,
        )

    props = affected.get("properties") or {}
    return FloodExtent(
        event_id=event_id,
        episode_id=episode_id,
        geometry=geometry,
        bbox=(minx, miny, maxx, maxy),
        # Degrees-squared, not km² — a rough size indicator only. Real area needs an equal-area
        # projection, which Phase 6 can add alongside the flood-polygon raster work.
        area_sq_deg=float(polygon.area),
        polygon_label=props.get("polygonlabel"),
        provenance=provenance,
    )


def _select_affected_feature(features: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the affected-area feature by its `Class` property, falling back to its label.

    Selection is by property rather than list position: the feature order is not documented and
    indexing into it would silently pick the centroid or the oversized global polygon.
    """
    candidates = list(features)
    for feature in candidates:
        if (feature.get("properties") or {}).get("Class") == AFFECTED_AREA_CLASS:
            return feature
    for feature in candidates:
        label = ((feature.get("properties") or {}).get("polygonlabel") or "").lower()
        if "affected" in label:
            return feature
    return None
