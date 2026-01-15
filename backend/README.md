# Flood Response Coordinator — Backend (Phase 1: data layer)

Plain Python functions against real disaster-data APIs. No LLM, no agents, no web server yet.

Phase 1 exists to de-risk everything downstream: API quirks (wrong bbox math, misread severity
levels) are far cheaper to debug without an LLM in the loop, and every agent built later inherits
whatever the data layer gets wrong.

## Sources

| Module | Source | Auth |
|---|---|---|
| `app/sources/gdacs.py` | GDACS flood event list + affected-area polygons | none |
| `app/sources/cmr_stac.py` | NASA CMR-STAC (`LANCEMODIS` flood granules) + GIBS tiles | none for search/tiles |
| `app/sources/worldpop.py` | WorldPop 1 km per-country population rasters | none |

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
traceable to a specific tool call and timestamp — the Fact-Check agent added in Phase 3 can only
enforce that if the data layer emits it, so it is built in from the start rather than retrofitted.

Corollary: **no function in this layer returns an estimated, interpolated, or fallback number.** If a
source fails, is empty, or returns something unparseable, it raises `SourceUnavailable`.

## Caching

Responses are cached to `data/cache/<source>/` with a sidecar recording fetch time and request URL.
TTLs: GDACS event list 15 min, GDACS geometry 24 h, CMR-STAC 1 h, WorldPop rasters permanent (static
annual products). Bypass with `--refresh` or `FLOOD_CACHE_DISABLED=1`.

## Tests

```bash
uv run pytest           # offline, replays committed fixtures
uv run pytest -m live   # opt-in, hits the real APIs
```
