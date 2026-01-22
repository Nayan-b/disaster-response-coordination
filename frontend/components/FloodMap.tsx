"use client";

/**
 * Map view: NASA GIBS flood imagery over a basemap, with the affected-area bbox drawn on top.
 *
 * Leaflet rather than MapLibre GL. Both are named in the project brief, and Leaflet is the better
 * fit here: everything this view needs is raster — a basemap, a GIBS raster overlay, and one
 * rectangle — so the WebGL vector pipeline buys nothing, while Leaflet's plain `<img>` tiles render
 * anywhere. (MapLibre 5 and 6 both failed to initialize their style during testing — canvas sized
 * correctly, getStyle() null, zero tile requests, and no error event — with WebGL2, Workers, and
 * tile-host reachability all separately confirmed working.)
 *
 * The GIBS tile template comes from the backend rather than being hardcoded, so the WMTS details —
 * layer id, tile matrix set, and the z/y/x ordering, row before column — live in one place. Leaflet's
 * `{z}/{y}/{x}` placeholders line up with that ordering as written.
 *
 * The controls are deliberately few. An overlay opacity slider and a "recentre" button are what a
 * reviewer actually reaches for: the flood layer hides the terrain under it, and fitting the extent
 * is the one navigation move worth a shortcut.
 */

import { Crosshair, Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { formatArea, formatBbox } from "@/lib/format";
import type { ExtentInfo, TileLayer } from "@/lib/types";

// CARTO's keyless raster tiles now render an "API KEY REQUIRED" watermark; OSM's need no key.
const BASEMAP_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const BASEMAP_ATTRIBUTION = "© OpenStreetMap contributors";

/** 1x1 transparent PNG — GIBS 404s tiles outside a layer's coverage, which is expected. */
const BLANK_TILE =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYGD4DwABDQEBpDp7WwAAAABJRU5ErkJggg==";

const EXTENT_COLOR = "#f97316";

export function FloodMap({
  tiles,
  extent,
  loading,
}: {
  tiles: TileLayer | null;
  extent: ExtentInfo | null;
  loading?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const floodLayer = useRef<L.TileLayer | null>(null);
  const extentLayer = useRef<L.Rectangle | null>(null);
  const [opacity, setOpacity] = useState(0.75);

  useEffect(() => {
    if (!container.current || map.current) return;

    const instance = L.map(container.current, {
      center: [20, 80],
      zoom: 3,
      worldCopyJump: true,
      zoomControl: false,
    });
    L.control.zoom({ position: "topright" }).addTo(instance);
    L.tileLayer(BASEMAP_URL, { attribution: BASEMAP_ATTRIBUTION, maxZoom: 18 }).addTo(instance);
    map.current = instance;

    return () => {
      instance.remove();
      map.current = null;
      floodLayer.current = null;
      extentLayer.current = null;
    };
  }, []);

  // GIBS flood imagery overlay.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    floodLayer.current?.remove();
    floodLayer.current = null;
    if (!tiles) return;

    floodLayer.current = L.tileLayer(tiles.url_template, {
      attribution: tiles.attribution,
      // GIBS serves these layers only to this zoom; past it keep showing the last level rather
      // than blanking the overlay.
      maxNativeZoom: tiles.max_zoom,
      maxZoom: 18,
      opacity,
      errorTileUrl: BLANK_TILE,
    }).addTo(instance);
    // `opacity` is applied on creation and adjusted below; it is not a dependency here, or every
    // slider nudge would tear down and refetch the whole tile layer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tiles]);

  useEffect(() => {
    floodLayer.current?.setOpacity(opacity);
  }, [opacity]);

  // Affected-area bbox outline + fit.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    extentLayer.current?.remove();
    extentLayer.current = null;
    if (!extent) return;

    const [minX, minY, maxX, maxY] = extent.bbox;
    // Leaflet takes [lat, lng]; the bbox is [minLon, minLat, maxLon, maxLat].
    const bounds = L.latLngBounds([minY, minX], [maxY, maxX]);
    extentLayer.current = L.rectangle(bounds, {
      color: EXTENT_COLOR,
      weight: 2,
      dashArray: "5 3",
      fillColor: EXTENT_COLOR,
      fillOpacity: 0.08,
    }).addTo(instance);

    instance.fitBounds(bounds, { padding: [48, 48], maxZoom: 8 });
  }, [extent]);

  const recentre = () => {
    const instance = map.current;
    if (!instance || !extent) return;
    const [minX, minY, maxX, maxY] = extent.bbox;
    instance.fitBounds(L.latLngBounds([minY, minX], [maxY, maxX]), {
      padding: [48, 48],
      maxZoom: 8,
    });
  };

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />

      {/* Status ribbon — says what the map is showing, or why it is empty. */}
      {!tiles ? (
        <div className="pointer-events-none absolute inset-x-0 top-3 z-500 flex justify-center">
          <span className="rounded-full bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm ring-1 ring-foreground/10 backdrop-blur">
            {loading ? "Loading flood imagery…" : "Start a run to load flood imagery"}
          </span>
        </div>
      ) : null}

      {/* Legend + controls */}
      {tiles ? (
        <div className="absolute bottom-3 left-3 z-500 w-64 max-w-[calc(100%-1.5rem)] space-y-2 rounded-lg bg-background/92 p-2.5 text-[11px] shadow-sm ring-1 ring-foreground/10 backdrop-blur">
          <div className="flex items-center gap-1.5">
            <Layers className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-medium text-foreground" title={tiles.layer}>
              {tiles.layer}
            </span>
            <span className="shrink-0 text-muted-foreground tabular-nums">{tiles.date}</span>
          </div>

          <label className="flex items-center gap-2 text-muted-foreground">
            <span className="shrink-0">Overlay</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={opacity}
              onChange={(event) => setOpacity(Number(event.target.value))}
              className="h-1 min-w-0 flex-1 accent-orange-500"
              aria-label="Flood imagery opacity"
            />
            <span className="w-8 shrink-0 text-right tabular-nums">
              {Math.round(opacity * 100)}%
            </span>
          </label>

          {extent ? (
            <div className="space-y-1 border-t pt-2 text-muted-foreground">
              <div className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="size-2.5 shrink-0 rounded-[2px] border border-dashed"
                  style={{ borderColor: EXTENT_COLOR, background: `${EXTENT_COLOR}14` }}
                />
                <span className="min-w-0 flex-1">Affected area · {formatArea(extent.area_sq_deg)}</span>
                <button
                  type="button"
                  onClick={recentre}
                  title="Fit the map to the affected area"
                  aria-label="Fit the map to the affected area"
                  className="shrink-0 rounded p-0.5 hover:bg-muted hover:text-foreground"
                >
                  <Crosshair className="size-3" />
                </button>
              </div>
              <div className="font-mono text-[10px] break-all">{formatBbox(extent.bbox)}</div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
