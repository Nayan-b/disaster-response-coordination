"""Cached HTTP layer shared by all three sources.

Every fetch returns `(payload, Provenance)` so no source module can accidentally produce a value
without recording where it came from.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import httpx

from app import cache, config
from app.models import Provenance, SourceName, SourceUnavailable


def _provenance(
    source: SourceName,
    item: cache.CachedItem,
    from_cache: bool,
    note: str | None = None,
) -> Provenance:
    return Provenance(
        source=source,
        request_url=item.request_url,
        fetched_at=item.fetched_at,
        from_cache=from_cache,
        cache_age_seconds=item.age_seconds if from_cache else None,
        note=note,
    )


def parse_json_lenient(raw: str | bytes, *, source: str, url: str) -> Any:
    """Parse JSON that may have trailing garbage appended.

    The WorldPop API leaks PHP warnings after the JSON body (`}<br /><b>Warning</b>: ...`), which
    breaks `json.loads`. `raw_decode` reads the leading JSON value and ignores the rest.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.lstrip()
    if not text:
        raise SourceUnavailable(source, "empty response body", url)
    try:
        value, _end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise SourceUnavailable(
            source, f"response was not valid JSON ({exc.msg}); first 200 chars: {text[:200]!r}", url
        ) from exc
    return value


def fetch_json(
    source: SourceName,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    ttl: int | None = None,
    refresh: bool = False,
    note: str | None = None,
) -> tuple[Any, Provenance]:
    """GET a JSON document, via cache unless `refresh` is set.

    Raises `SourceUnavailable` on any transport error, non-2xx status, or unparseable body — never
    returns a partial or substituted payload.
    """
    key = cache.request_key(url, params)

    if not refresh:
        hit = cache.read(source, key, ".json", ttl)
        if hit is not None:
            payload = parse_json_lenient(hit.read_bytes(), source=source, url=hit.request_url)
            return payload, _provenance(source, hit, from_cache=True, note=note)

    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json, */*"}
    try:
        response = httpx.get(
            url,
            params=dict(params or {}),
            headers=headers,
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SourceUnavailable(source, f"request failed: {type(exc).__name__}: {exc}", url) from exc

    final_url = str(response.request.url)
    if response.status_code >= 400:
        raise SourceUnavailable(
            source, f"HTTP {response.status_code} {response.reason_phrase}", final_url
        )

    payload = parse_json_lenient(response.content, source=source, url=final_url)
    item = cache.write(
        source,
        key,
        response.content,
        request_url=final_url,
        suffix=".json",
        status_code=response.status_code,
    )
    return payload, _provenance(source, item, from_cache=False, note=note)


def fetch_file(
    source: SourceName,
    url: str,
    *,
    suffix: str = ".bin",
    ttl: int | None = config.TTL_PERMANENT,
    refresh: bool = False,
    note: str | None = None,
    on_progress: Any | None = None,
) -> tuple[Path, Provenance]:
    """Download a (potentially large) binary file into the cache, streaming to disk.

    Used for WorldPop rasters. They are tens of MB and WorldPop does not honor HTTP range requests
    — `Accept-Ranges: bytes` is advertised but a ranged request returns the full body with HTTP 200
    — so windowed reads are impossible and the whole file must be cached locally.
    """
    key = cache.request_key(url)

    if not refresh:
        hit = cache.read(source, key, suffix, ttl)
        if hit is not None and hit.path.stat().st_size > 0:
            return hit.path, _provenance(source, hit, from_cache=True, note=note)

    payload_path, _meta_path = cache.paths_for(source, key, suffix)
    tmp = payload_path.with_suffix(payload_path.suffix + ".partial")
    headers = {"User-Agent": config.USER_AGENT}

    try:
        with httpx.stream(
            "GET", url, headers=headers, timeout=config.DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as response:
            if response.status_code >= 400:
                raise SourceUnavailable(
                    source, f"HTTP {response.status_code} {response.reason_phrase}", url
                )
            total = int(response.headers.get("content-length") or 0)
            written = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 256):
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress is not None:
                        on_progress(written, total)
            status_code = response.status_code
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise SourceUnavailable(source, f"download failed: {type(exc).__name__}: {exc}", url) from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    if written == 0:
        tmp.unlink(missing_ok=True)
        raise SourceUnavailable(source, "download returned zero bytes", url)

    tmp.replace(payload_path)
    item = cache.write_meta_for_existing(
        source, key, request_url=url, suffix=suffix, status_code=status_code
    )
    return payload_path, _provenance(source, item, from_cache=False, note=note)


def parse_gdacs_datetime(value: Any) -> datetime | None:
    """Parse a GDACS timestamp, which is naive ISO with no timezone, as UTC.

    GDACS returns e.g. `"2025-06-15T01:00:00"`. Leaving these naive would make downstream
    comparisons against timezone-aware `now()` raise, and silently mis-order events.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
