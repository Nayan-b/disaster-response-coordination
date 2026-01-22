"""Configuration for the Phase 1 data layer.

Everything here is overridable by environment variable so the same code runs from the CLI, from
tests, and later from the FastAPI service without edits.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CACHE_DIR = Path(os.environ.get("FLOOD_CACHE_DIR", REPO_ROOT / "data" / "cache"))

CACHE_DISABLED = os.environ.get("FLOOD_CACHE_DISABLED", "").lower() in {
    "1",
    "true",
    "yes",
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

# GDACS rejects or throttles requests without a browser-like User-Agent.
USER_AGENT = os.environ.get(
    "FLOOD_USER_AGENT",
    "Mozilla/5.0 (compatible; FloodResponseCoordinator/0.1; +https://github.com/)",
)

HTTP_TIMEOUT = float(os.environ.get("FLOOD_HTTP_TIMEOUT", "60"))

# Rasters are tens of MB; give downloads their own, longer budget.
DOWNLOAD_TIMEOUT = float(os.environ.get("FLOOD_DOWNLOAD_TIMEOUT", "600"))


# ---------------------------------------------------------------------------
# Cache TTLs, in seconds
# ---------------------------------------------------------------------------

TTL_GDACS_EVENTS = int(os.environ.get("FLOOD_TTL_GDACS_EVENTS", 15 * 60))

# Affected-area geometry is ~880 KB per event and changes only when a new episode is published.
TTL_GDACS_GEOMETRY = int(os.environ.get("FLOOD_TTL_GDACS_GEOMETRY", 24 * 60 * 60))

TTL_CMR_STAC = int(os.environ.get("FLOOD_TTL_CMR_STAC", 60 * 60))

TTL_WORLDPOP_METADATA = int(
    os.environ.get("FLOOD_TTL_WORLDPOP_METADATA", 7 * 24 * 60 * 60)
)

# WorldPop rasters are static annual products — never expire them.
TTL_PERMANENT = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Event-list defaults. The lookback window is short on purpose: GDACS flood events stay in the
# list for weeks after their window closes, so a wide window buries what is happening now under
# events that have already passed. The result cap keeps triage focused on the most urgent handful —
# it is applied *after* ranking by alert level, and a truncated listing always says so.
EVENTS_DEFAULT_LOOKBACK_DAYS = int(os.environ.get("FLOOD_EVENTS_LOOKBACK_DAYS", "5"))
EVENTS_MAX_RESULTS = int(os.environ.get("FLOOD_EVENTS_MAX_RESULTS", "10"))

GDACS_EVENTLIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_GEOMETRY_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"

CMR_STAC_BASE = "https://cmr.earthdata.nasa.gov/stac"
# The MODIS/VIIRS near-real-time global flood products live under this CMR provider.
CMR_FLOOD_PROVIDER = "LANCEMODIS"
# The 3-day composite, chosen as the default for two reasons: it serves GeoTIFF (the bare
# MCDWD_L3_NRT_6.1 collection serves HDF, which needs extra tooling to read), and a 3-day window
# survives cloud cover better than a 1-day one. It also pairs with the GIBS 3-day layer below.
CMR_DEFAULT_FLOOD_COLLECTION = "MCDWD_L3_F3_NRT_6.1"

GIBS_TILE_BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
GIBS_DEFAULT_FLOOD_LAYER = "MODIS_Combined_Flood_3-Day"
# Verified from the GIBS WMTS capabilities document for the flood layers.
GIBS_TILE_MATRIX_SET = "GoogleMapsCompatible_Level9"
# Earliest date the GIBS flood layers carry imagery for (capabilities: 2021-01-01/present, P1D).
GIBS_EARLIEST_DATE = "2021-01-01"

WORLDPOP_HUB_BASE = "https://hub.worldpop.org/rest/data/pop"
# Unconstrained per-country, 1 km. Chosen over the 100 m product because file sizes are ~100x
# smaller (Nepal 0.9 MB vs 92 MB) and WorldPop does not honor HTTP range requests, so the whole
# file must be downloaded either way.
WORLDPOP_DEFAULT_ALIAS = "wpic1km"
WORLDPOP_DEFAULT_YEAR = int(os.environ.get("FLOOD_WORLDPOP_YEAR", "2020"))
