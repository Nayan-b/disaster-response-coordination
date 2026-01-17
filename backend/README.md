# Backend — the data layer

Plain Python functions against real disaster-data APIs, with no LLM involved. This is the foundation
everything else sits on; for the agent graph, the API service, and how to run the whole stack, see
the [root README](../README.md).

It was built first, deliberately: API quirks (wrong bbox math, misread severity levels) are far
cheaper to debug without an LLM in the loop, and every agent above it inherits whatever the data
layer gets wrong. The findings below are the reason that ordering paid off.

## Sources

| Module | Source | Auth |
|---|---|---|
| `app/sources/gdacs.py` | GDACS flood event list + affected-area polygons | none |
| `app/sources/cmr_stac.py` | NASA CMR-STAC (`LANCEMODIS` flood granules) + GIBS tiles | none for search/tiles |
| `app/sources/worldpop.py` | WorldPop 1 km per-country population rasters | none |

## Findings that shaped the design

Discovered by probing the live APIs. Each is enforced in code and pinned by a test, so everything
built on top inherits the correction rather than rediscovering it.

**Use the UN-adjusted WorldPop product.** The unconstrained product overstates national totals by an
amount that varies by country, so it would inflate every affected-population figure:

| country | `wpicuadj1km` (UN-adj) | `wpic1km` (unconstrained) | UN figure |
|---|---:|---:|---:|
| Nepal | 29,136,808 | 40,297,824 | ~29.1M |
| Pakistan | 220,892,352 | 231,216,336 | ~220M |
| China | 1,439,323,776 | 1,444,326,144 | ~1.41B |

**Population is a sum, not a mean.** Pixels hold counts of people, so averaging understates a flood
zone by orders of magnitude. Nodata is `-99999.0`, and a naive sum over Nepal's raster gives `-2.8e10`.

**The GDACS event-list bbox is a zero-area centroid**, so `FloodEvent` deliberately exposes no bbox.
Real extents come from the geometry endpoint's `Poly_Affected` MultiPolygon, with the bbox derived
from its bounds.

**Select by property, not by position.** The GDACS geometry endpoint returns the centroid and the
much larger `Poly_Global` alongside `Poly_Affected` in undocumented order. Likewise, CMR-STAC asset
keys are dynamic production timestamps, so data assets are matched on href extension.

**CMR's NRT archive is short; GIBS is the historical path.** Queries for past events legitimately
return zero granules — reported as unavailable, never backfilled from a different date. GIBS flood
tiles cover 2021-01-01 to present with no auth, which is what makes replay demos viable. Its WMTS
path order is `z/y/x` — row before column.

**Two upstream quirks worked around:** WorldPop's async stats API never completes (status stays
`"created"`) and leaks PHP warnings into the JSON body, hence local raster math and a lenient JSON
parser. And `files[0]` in a WorldPop record is not always a raster — Pakistan's lists an
`ASCII_XYZ.zip`.

## Setup

```bash
uv sync --extra dev
```

## CLI

```bash
uv run python -m app.cli events     --days 60 --alert-level Orange,Red
uv run python -m app.cli extent     --event-id 1103339 --episode-id 33
uv run python -m app.cli imagery    --event-id 1103339 --episode-id 33
uv run python -m app.cli population --event-id 1103339 --episode-id 33
uv run python -m app.cli report     --event-id 1103339 --episode-id 33
```

`report` is the reviewable artifact: all three sources side by side with a provenance row per number.

## Provenance

Every value returned by this layer carries a `Provenance` record (source, exact request URL, UTC
fetch timestamp, cache age). The project requires that every number reaching a human responder be
traceable to a specific tool call and timestamp — the Fact-Check agent can only enforce that if the
data layer emits it, so it was built in from the start rather than retrofitted.

Corollary: **no function in this layer returns an estimated, interpolated, or fallback number.** If a
source fails, is empty, or returns something unparseable, it raises `SourceUnavailable`.

## Caching

Responses are cached to `data/cache/<source>/` with a sidecar recording fetch time and request URL.
TTLs: GDACS event list 15 min, GDACS geometry 24 h, CMR-STAC 1 h, WorldPop rasters permanent (static
annual products). Bypass with `--refresh` or `FLOOD_CACHE_DISABLED=1`.

## Tests

```bash
uv run pytest           # 108 offline tests, replays committed fixtures
uv run pytest -m live   # 9 opt-in tests against the real APIs
```

Fixtures are deliberately hostile — hostile feature ordering, a zip before the raster, dynamic asset
keys — so a regression to a naive implementation cannot pass.

## What sits on top

These functions are exposed as MCP tools with no change to their signatures (`app/tools.py`,
`app/mcp_server.py`), and the LangGraph agents call the same registry. The `Provenance` records here
are the tool-call receipts the Fact-Check agent verifies claims against — which is why this layer
records where every value came from rather than just returning the value.
