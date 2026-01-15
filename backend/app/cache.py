"""Disk cache for API responses and downloaded rasters.

Two jobs:

1. Keep development from hammering public APIs (GDACS, CMR, WorldPop are free and unmetered — being
   a good citizen is on us).
2. Record *when* each payload was fetched, which is what populates `Provenance.fetched_at`. A cache
   hit must report the original fetch time, not the read time, or stale data would look fresh.

Entries are content-addressed by a hash of the canonical request, with a JSON sidecar holding the
metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from app import config


@dataclass
class CachedItem:
    """A payload on disk plus the metadata needed to build a `Provenance`."""

    path: Path
    request_url: str
    fetched_at: datetime
    age_seconds: float
    status_code: int | None = None
    extra: dict[str, Any] | None = None

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


def request_key(url: str, params: Mapping[str, Any] | None = None) -> str:
    """Stable hash of a request, so the same query always maps to the same cache entry."""
    canonical = url
    if params:
        # Sorted so param ordering never splits one logical request across two cache entries.
        pairs = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
        canonical += "?" + "&".join(f"{k}={v}" for k, v in pairs)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _source_dir(source: str) -> Path:
    path = config.CACHE_DIR / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def paths_for(source: str, key: str, suffix: str = ".json") -> tuple[Path, Path]:
    """Return `(payload_path, meta_path)` for a cache entry."""
    directory = _source_dir(source)
    return directory / f"{key}{suffix}", directory / f"{key}.meta.json"


def read(
    source: str,
    key: str,
    suffix: str = ".json",
    ttl: int | None = None,
) -> CachedItem | None:
    """Return a live cache entry, or None if absent, expired, or unreadable.

    `ttl=None` means the entry never expires (used for WorldPop's static annual rasters).
    """
    if config.CACHE_DISABLED:
        return None

    payload_path, meta_path = paths_for(source, key, suffix)
    if not payload_path.exists() or not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text())
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        # A corrupt sidecar is treated as a miss rather than an error — the fetch layer will
        # overwrite it. Never guess a fetch time.
        return None

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)

    age = (datetime.now(UTC) - fetched_at).total_seconds()
    if ttl is not None and age > ttl:
        return None

    return CachedItem(
        path=payload_path,
        request_url=meta.get("request_url", ""),
        fetched_at=fetched_at,
        age_seconds=age,
        status_code=meta.get("status_code"),
        extra=meta.get("extra"),
    )


def write(
    source: str,
    key: str,
    data: bytes,
    request_url: str,
    suffix: str = ".json",
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> CachedItem:
    """Persist a payload and its metadata sidecar."""
    payload_path, meta_path = paths_for(source, key, suffix)
    fetched_at = datetime.now(UTC)

    # Write via a temp file so an interrupted run can't leave a truncated payload that later looks
    # like a valid cache hit.
    tmp = payload_path.with_suffix(payload_path.suffix + ".partial")
    tmp.write_bytes(data)
    tmp.replace(payload_path)

    meta_path.write_text(
        json.dumps(
            {
                "request_url": request_url,
                "fetched_at": fetched_at.isoformat(),
                "status_code": status_code,
                "bytes": len(data),
                "extra": extra or {},
            },
            indent=2,
        )
    )
    return CachedItem(
        path=payload_path,
        request_url=request_url,
        fetched_at=fetched_at,
        age_seconds=0.0,
        status_code=status_code,
        extra=extra,
    )


def write_meta_for_existing(
    source: str,
    key: str,
    request_url: str,
    suffix: str,
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> CachedItem:
    """Write the sidecar for a payload already streamed to disk (large raster downloads)."""
    payload_path, meta_path = paths_for(source, key, suffix)
    fetched_at = datetime.now(UTC)
    meta_path.write_text(
        json.dumps(
            {
                "request_url": request_url,
                "fetched_at": fetched_at.isoformat(),
                "status_code": status_code,
                "bytes": payload_path.stat().st_size if payload_path.exists() else 0,
                "extra": extra or {},
            },
            indent=2,
        )
    )
    return CachedItem(
        path=payload_path,
        request_url=request_url,
        fetched_at=fetched_at,
        age_seconds=0.0,
        status_code=status_code,
        extra=extra,
    )


def stats() -> dict[str, Any]:
    """Cache contents by source — handy for the CLI and for spotting runaway raster downloads."""
    if not config.CACHE_DIR.exists():
        return {"total_bytes": 0, "sources": {}}

    sources: dict[str, dict[str, Any]] = {}
    total = 0
    for source_dir in sorted(p for p in config.CACHE_DIR.iterdir() if p.is_dir()):
        entries = [p for p in source_dir.iterdir() if not p.name.endswith(".meta.json")]
        size = sum(p.stat().st_size for p in entries)
        total += size
        sources[source_dir.name] = {"entries": len(entries), "bytes": size}
    return {"total_bytes": total, "sources": sources}


def purge(source: str | None = None) -> int:
    """Delete cache entries; returns the number of files removed."""
    target = config.CACHE_DIR / source if source else config.CACHE_DIR
    if not target.exists():
        return 0
    removed = 0
    for path in sorted(target.rglob("*")):
        if path.is_file():
            path.unlink()
            removed += 1
    return removed
