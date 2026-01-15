"""Shared schema for the Phase 1 data layer.

Two ideas drive this module:

1. **Provenance is not optional.** Every number that can reach a human responder carries the source,
   the exact request URL, and the UTC timestamp it was fetched. The project requires that any figure
   in a recommendation be traceable to a specific tool call and timestamp; the Fact-Check agent
   (Phase 3) verifies claims against these records rather than re-deriving trust.

2. **Missing data is an error, never an estimate.** Any source that fails, returns nothing, or
   returns something unparseable raises `SourceUnavailable`. Nothing in this layer interpolates,
   guesses, or substitutes a fallback number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceName = Literal["gdacs", "cmr_stac", "gibs", "worldpop"]


class SourceUnavailable(Exception):
    """A data source could not supply the requested data.

    Raised instead of returning a degraded or estimated value, so that callers (and eventually the
    Allocation agent) are forced to say "this data is unavailable" out loud.
    """

    def __init__(
        self,
        source: str,
        reason: str,
        attempted_url: str | None = None,
        *,
        recoverable: bool = False,
    ) -> None:
        self.source = source
        self.reason = reason
        self.attempted_url = attempted_url
        # True when a different query might succeed (e.g. NRT archive too short for a historical
        # date) as opposed to a hard failure. Lets callers offer a fallback without hiding the gap.
        self.recoverable = recoverable
        detail = f"[{source}] {reason}"
        if attempted_url:
            detail += f" (url: {attempted_url})"
        super().__init__(detail)


class Provenance(BaseModel):
    """Where a value came from, and when."""

    source: SourceName
    request_url: str
    # When the data actually left the network. For a cache hit this is the ORIGINAL fetch time,
    # not the time of the cache read — otherwise stale data would look fresh.
    fetched_at: datetime
    from_cache: bool = False
    cache_age_seconds: float | None = None
    # Free-text caveat surfaced to the responder, e.g. "population raster is 2020, event is 2024".
    note: str | None = None

    def describe(self) -> str:
        stamp = self.fetched_at.strftime("%Y-%m-%d %H:%M:%SZ")
        origin = "cache" if self.from_cache else "live"
        text = f"{self.source} ({origin}) @ {stamp}"
        if self.from_cache and self.cache_age_seconds is not None:
            text += f", age {self.cache_age_seconds / 60:.0f}m"
        if self.note:
            text += f" — {self.note}"
        return text


class AffectedCountry(BaseModel):
    iso2: str | None = None
    iso3: str | None = None
    name: str | None = None


class FloodEvent(BaseModel):
    """A GDACS flood event, normalized.

    Deliberately has no `bbox`: the GDACS event list reports a zero-area centroid as the bbox for
    most events (e.g. `[-117.02, 34.89, -117.02, 34.89]`), and computing population over that
    would be meaningless. A usable extent comes from `gdacs.get_flood_extent()`.
    """

    event_id: int
    episode_id: int | None = None
    event_type: str = "FL"
    name: str
    country: str | None = None
    iso3: str | None = None
    affected_countries: list[AffectedCountry] = Field(default_factory=list)

    # GDACS reports two alert levels and they can disagree: an event may be overall "Orange" while
    # its latest episode is "Green". Keep both; never collapse them into one.
    alert_level: str | None = None
    alert_score: float | None = None
    episode_alert_level: str | None = None
    episode_alert_score: float | None = None

    # Naive ISO strings in the API response, interpreted as UTC.
    from_date: datetime | None = None
    to_date: datetime | None = None
    date_modified: datetime | None = None

    severity_value: float | None = None
    severity_text: str | None = None
    severity_unit: str | None = None

    centroid: tuple[float, float] | None = None
    is_current: bool | None = None
    is_temporary: bool | None = None
    source_model: str | None = None  # GDACS's own upstream model, e.g. "GLOFAS"

    report_url: str | None = None
    geometry_url: str | None = None
    details_url: str | None = None

    provenance: Provenance


class FloodExtent(BaseModel):
    """The affected-area polygon for one event episode, plus the bbox derived from it."""

    event_id: int
    episode_id: int
    # GeoJSON geometry (Polygon or MultiPolygon) of the GDACS `Poly_Affected` feature.
    geometry: dict[str, Any]
    # Derived from the polygon bounds — never taken from the event list's centroid bbox.
    bbox: tuple[float, float, float, float]
    area_sq_deg: float
    polygon_label: str | None = None
    provenance: Provenance

    @property
    def bbox_string(self) -> str:
        """Comma-separated bbox in the order STAC APIs expect."""
        return ",".join(str(round(v, 4)) for v in self.bbox)


class ImageryGranule(BaseModel):
    granule_id: str
    collection: str | None = None
    datetime_utc: datetime | None = None
    # Coarse 10x10-degree product tiles — a match does not mean the imagery is framed on the event.
    bbox: tuple[float, float, float, float] | None = None
    data_href: str | None = None
    # "tif" for the F1/F2/F3/F1C composite collections, "hdf" for the base MCDWD_L3_NRT collection.
    data_format: str | None = None
    browse_href: str | None = None
    metadata_href: str | None = None


class ImageryResult(BaseModel):
    collection: str
    bbox_queried: tuple[float, float, float, float]
    start: datetime
    end: datetime
    matched: int | None = None
    granules: list[ImageryGranule] = Field(default_factory=list)
    # Granule downloads require an Earthdata login; Phase 1 returns hrefs and downloads nothing.
    requires_auth_to_download: bool = True
    provenance: Provenance


class TileLayer(BaseModel):
    """A ready-to-use GIBS raster tile layer for the map UI (Phase 5)."""

    layer: str
    date: str
    # WMTS path order is {TileMatrix}/{TileRow}/{TileCol} = z/y/x — row BEFORE column.
    url_template: str
    tile_matrix_set: str
    attribution: str = "NASA GIBS / LANCE"
    max_zoom: int = 8
    provenance: Provenance


class PopulationEstimate(BaseModel):
    """Population inside a geometry, summed from a WorldPop persons-per-pixel raster."""

    population: float
    iso3_covered: list[str] = Field(default_factory=list)
    iso3_missing: list[str] = Field(default_factory=list)
    raster_year: int
    resolution: str
    # Sanity-check handles: a wildly wrong pixel count usually means the geometry or CRS is off.
    pixels_counted: int
    # Pixels INSIDE the polygon carrying no data — a genuine coverage gap. Pixels outside the
    # polygon are excluded from this count, so it is not inflated by the bbox/polygon difference.
    nodata_pixels_skipped: int = 0
    per_country: dict[str, float] = Field(default_factory=dict)
    provenance: Provenance

    @property
    def is_partial(self) -> bool:
        """True when some affected country had no raster, so the sum understates reality."""
        return bool(self.iso3_missing)


class ToolCall(BaseModel):
    """One tool invocation and its result — the unit of evidence for fact-checking.

    The Allocation agent may only state figures that appear in a successful `ToolCall.result`. The
    Fact-Check agent enforces that by matching claims against these records, so this is the audit
    trail that makes the guardrail mechanical rather than aspirational.
    """

    call_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    called_at: datetime
    ok: bool

    def describe(self) -> str:
        status = "ok" if self.ok else f"unavailable: {self.result.get('reason', '?')}"
        args = ", ".join(f"{k}={v}" for k, v in self.arguments.items() if v is not None)
        return f"{self.call_id} {self.tool}({args}) → {status} @ {self.called_at:%Y-%m-%d %H:%M:%SZ}"


class Claim(BaseModel):
    """One assertion the Allocation agent wants to make, with the tool call it came from.

    `source_call_id` is mandatory for any claim carrying a `value`. The Fact-Check agent resolves it
    against the session's `ToolCall` log and rejects the claim if the number is not actually there —
    which is how "every number must be traceable to a tool call and timestamp" becomes enforceable
    instead of merely requested.
    """

    claim_id: str
    text: str
    value: float | None = None
    unit: str | None = None
    source_call_id: str | None = None
    # Set by the Fact-Check agent, never by the drafting agent.
    verified: bool = False
    rejection_reason: str | None = None


class AllocationAction(BaseModel):
    """A single proposed action inside a recommendation. Never executed by this system."""

    priority: int
    action: str
    rationale: str
    supporting_claim_ids: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    """A draft resource-allocation recommendation, pending human review.

    Framed as a recommendation everywhere — in the field names, the UI copy, and the system prompt.
    Nothing in this system executes an action; approval only records a human's decision.
    """

    headline: str
    situation: str
    actions: list[AllocationAction] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    # "claude-opus-5" or "rule-based" — a responder should know what drafted this.
    drafted_by: str
    drafted_at: datetime
    requires_human_approval: bool = True


class FactCheckResult(BaseModel):
    """Outcome of verifying a draft's claims against the tool-call log."""

    verified_claims: list[Claim] = Field(default_factory=list)
    rejected_claims: list[Claim] = Field(default_factory=list)
    # True when nothing unverifiable survived, so the draft may go to a human.
    passed: bool = True
    blocking_reason: str | None = None

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_claims)


class ApprovalDecision(BaseModel):
    """A human responder's decision on a recommendation."""

    decision: Literal["approved", "edited", "rejected"]
    reviewer: str
    decided_at: datetime
    notes: str | None = None
    # Present when decision == "edited": the responder's replacement text.
    edited_actions: list[AllocationAction] | None = None


class EventReport(BaseModel):
    """All three sources for one event, assembled for human review.

    Sub-results are optional: a missing section means that source was unavailable, and the reason is
    recorded in `unavailable` rather than being filled in with a guess.
    """

    event: FloodEvent
    extent: FloodExtent | None = None
    imagery: ImageryResult | None = None
    tiles: TileLayer | None = None
    population: PopulationEstimate | None = None
    unavailable: dict[str, str] = Field(default_factory=dict)

    def provenance_records(self) -> list[tuple[str, Provenance]]:
        """Every figure in this report paired with the tool call it came from."""
        records: list[tuple[str, Provenance]] = [("event", self.event.provenance)]
        if self.extent:
            records.append(("flood extent / bbox", self.extent.provenance))
        if self.imagery:
            records.append(("imagery granules", self.imagery.provenance))
        if self.tiles:
            records.append(("tile layer", self.tiles.provenance))
        if self.population:
            records.append(("population", self.population.provenance))
        return records
