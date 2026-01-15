"""Shared test fixtures.

Offline tests replay committed JSON fixtures by patching each source's `fetch_json`, so the parsing
and correctness logic is tested without network access. Tests marked `live` hit the real APIs and are
opt-in (`pytest -m live`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.models import Provenance

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        source="gdacs",
        request_url="https://example.test/fixture",
        fetched_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        from_cache=False,
    )


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch) -> Path:
    """Point the cache at a temp dir so tests never touch the developer's real cache."""
    from app import config

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config, "CACHE_DISABLED", False)
    return cache_dir


@pytest.fixture
def fixture_fetch(monkeypatch):
    """Patch a module's `fetch_json` to return a fixture payload.

    Usage: `fixture_fetch(gdacs, "gdacs_eventlist.json")`, optionally with `payload=` to pass a dict
    directly. Returns a list that records the `(url, params)` of each call.
    """

    def install(module, fixture_name: str | None = None, *, payload: Any = None):
        data = payload if fixture_name is None else load_fixture(fixture_name)
        calls: list[tuple[str, dict]] = []

        def fake_fetch_json(source, url, params=None, *, ttl=None, refresh=False, note=None):
            calls.append((url, dict(params or {})))
            return data, Provenance(
                source=source,
                request_url=url,
                fetched_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                from_cache=False,
                note=note,
            )

        monkeypatch.setattr(module, "fetch_json", fake_fetch_json)
        return calls

    return install


@pytest.fixture
def population_raster(tmp_path) -> Path:
    """A small synthetic persons-per-pixel raster with a nodata hole.

    Grid is 4x4 one-degree pixels spanning lon 0-4, lat 0-4 (north-up). Every valid pixel holds 10
    people; two pixels carry WorldPop's -99999.0 nodata sentinel. Total of valid pixels = 140.
    """
    path = tmp_path / "synthetic_pop.tif"
    values = np.full((4, 4), 10.0, dtype="float32")
    values[0, 0] = -99999.0
    values[3, 3] = -99999.0

    transform = from_origin(0.0, 4.0, 1.0, 1.0)  # west edge lon 0, north edge lat 4, 1 degree pixels
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-99999.0,
    ) as dst:
        dst.write(values, 1)
    return path
