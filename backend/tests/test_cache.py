"""Cache and fetch-layer tests.

The provenance-related behavior here is what makes downstream claim-checking trustworthy: if a cache
hit reported the read time instead of the original fetch time, week-old data would look fresh.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app import cache
from app.fetch import parse_gdacs_datetime, parse_json_lenient
from app.models import SourceUnavailable


def test_request_key_is_order_independent():
    """Param ordering must not split one logical request across two cache entries."""
    a = cache.request_key("https://x.test/api", {"b": 2, "a": 1})
    b = cache.request_key("https://x.test/api", {"a": 1, "b": 2})
    assert a == b


def test_request_key_ignores_none_params():
    assert cache.request_key("https://x.test", {"a": 1, "b": None}) == cache.request_key(
        "https://x.test", {"a": 1}
    )


def test_request_key_distinguishes_different_requests():
    assert cache.request_key("https://x.test", {"a": 1}) != cache.request_key(
        "https://x.test", {"a": 2}
    )


def test_roundtrip_preserves_payload_and_url(isolated_cache):
    cache.write("gdacs", "k1", b'{"hello": 1}', request_url="https://x.test/a")
    hit = cache.read("gdacs", "k1", ttl=3600)

    assert hit is not None
    assert json.loads(hit.read_bytes()) == {"hello": 1}
    assert hit.request_url == "https://x.test/a"


def test_miss_when_absent(isolated_cache):
    assert cache.read("gdacs", "never-written", ttl=3600) is None


def test_expired_entry_is_a_miss(isolated_cache):
    cache.write("gdacs", "k2", b"{}", request_url="https://x.test")
    payload_path, meta_path = cache.paths_for("gdacs", "k2")

    stale = datetime.now(UTC) - timedelta(hours=2)
    meta = json.loads(meta_path.read_text())
    meta["fetched_at"] = stale.isoformat()
    meta_path.write_text(json.dumps(meta))

    assert cache.read("gdacs", "k2", ttl=60) is None
    # The same entry is still valid under a longer TTL.
    assert cache.read("gdacs", "k2", ttl=3 * 3600) is not None
    assert payload_path.exists()


def test_ttl_none_never_expires(isolated_cache):
    """WorldPop rasters are static annual products and must not be re-downloaded."""
    cache.write("worldpop", "k3", b"raster", request_url="https://x.test", suffix=".tif")
    _payload, meta_path = cache.paths_for("worldpop", "k3", ".tif")

    ancient = datetime.now(UTC) - timedelta(days=900)
    meta = json.loads(meta_path.read_text())
    meta["fetched_at"] = ancient.isoformat()
    meta_path.write_text(json.dumps(meta))

    assert cache.read("worldpop", "k3", ".tif", ttl=None) is not None


def test_hit_reports_original_fetch_time_and_age(isolated_cache):
    """A cache hit must not look freshly fetched, or stale data would pass as current."""
    cache.write("gdacs", "k4", b"{}", request_url="https://x.test")
    _payload, meta_path = cache.paths_for("gdacs", "k4")

    fetched = datetime.now(UTC) - timedelta(minutes=30)
    meta = json.loads(meta_path.read_text())
    meta["fetched_at"] = fetched.isoformat()
    meta_path.write_text(json.dumps(meta))

    hit = cache.read("gdacs", "k4", ttl=24 * 3600)
    assert abs((hit.fetched_at - fetched).total_seconds()) < 2
    assert 1700 < hit.age_seconds < 1900  # ~30 minutes


def test_corrupt_sidecar_is_a_miss_not_a_crash(isolated_cache):
    cache.write("gdacs", "k5", b"{}", request_url="https://x.test")
    _payload, meta_path = cache.paths_for("gdacs", "k5")
    meta_path.write_text("{not json")

    assert cache.read("gdacs", "k5", ttl=3600) is None


def test_missing_fetched_at_is_a_miss(isolated_cache):
    """Never invent a fetch time for an entry that lost it."""
    cache.write("gdacs", "k6", b"{}", request_url="https://x.test")
    _payload, meta_path = cache.paths_for("gdacs", "k6")
    meta_path.write_text(json.dumps({"request_url": "https://x.test"}))

    assert cache.read("gdacs", "k6", ttl=3600) is None


def test_partial_files_are_not_left_behind(isolated_cache):
    cache.write("gdacs", "k7", b"payload", request_url="https://x.test")
    entries = sorted(p.name for p in (isolated_cache / "gdacs").iterdir())
    assert not any(name.endswith(".partial") for name in entries)


def test_cache_disabled_forces_a_miss(isolated_cache, monkeypatch):
    from app import config

    cache.write("gdacs", "k8", b"{}", request_url="https://x.test")
    monkeypatch.setattr(config, "CACHE_DISABLED", True)
    assert cache.read("gdacs", "k8", ttl=3600) is None


def test_stats_and_purge(isolated_cache):
    cache.write("gdacs", "s1", b"12345", request_url="https://x.test")
    cache.write("worldpop", "s2", b"123", request_url="https://x.test", suffix=".tif")

    info = cache.stats()
    assert info["sources"]["gdacs"]["entries"] == 1
    assert info["total_bytes"] == 8

    assert cache.purge("gdacs") == 2  # payload + sidecar
    assert cache.stats()["sources"].get("gdacs", {"entries": 0})["entries"] == 0
    assert cache.stats()["sources"]["worldpop"]["entries"] == 1


# --- lenient JSON parsing ---------------------------------------------------


def test_parses_json_with_trailing_php_warnings():
    """WorldPop appends PHP warnings after the JSON body, which breaks json.loads outright."""
    raw = (
        '{"status": "created", "taskid": "abc"}<br />\n'
        "<b>Warning</b>: Trying to access array offset on value of type bool in "
        "<b>/srv/www/api.worldpop.org/html/app/TasksController.php</b> on line <b>40</b><br />"
    )
    assert parse_json_lenient(raw, source="worldpop", url="x")["taskid"] == "abc"


def test_parses_bytes_and_leading_whitespace():
    assert parse_json_lenient(b'  {"a": 1}', source="x", url="y") == {"a": 1}


def test_empty_body_raises():
    with pytest.raises(SourceUnavailable, match="empty response body"):
        parse_json_lenient("   ", source="x", url="y")


def test_unparseable_body_raises_with_a_preview():
    with pytest.raises(SourceUnavailable) as excinfo:
        parse_json_lenient("<html>503 Service Unavailable</html>", source="x", url="y")

    # The snippet is what makes an HTML error page diagnosable from a log.
    assert "503" in excinfo.value.reason


# --- timestamp normalization ------------------------------------------------


def test_naive_timestamp_treated_as_utc():
    parsed = parse_gdacs_datetime("2025-06-15T01:00:00")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.hour == 1


def test_zulu_timestamp_parsed():
    assert parse_gdacs_datetime("2026-08-13T00:00:00.000Z").hour == 0


def test_bad_timestamps_return_none():
    """A malformed date must not crash a parse — the field is simply absent."""
    assert parse_gdacs_datetime("not-a-date") is None
    assert parse_gdacs_datetime("") is None
    assert parse_gdacs_datetime(None) is None
