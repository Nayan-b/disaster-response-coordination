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
 */

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import type { ExtentInfo, TileLayer } from "@/lib/types";

const BASEMAP_URL = "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png";
const BASEMAP_ATTRIBUTION = "© OpenStreetMap contributors, © CARTO";

/** 1x1 transparent PNG — GIBS 404s tiles outside a layer's coverage, which is expected. */
const BLANK_TILE =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYGD4DwABDQEBpDp7WwAAAABJRU5ErkJggg==";

export function FloodMap({
  tiles,
  extent,
}: {
  tiles: TileLayer | null;
  extent: ExtentInfo | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const floodLayer = useRef<L.TileLayer | null>(null);
  const extentLayer = useRef<L.Rectangle | null>(null);

  useEffect(() => {
    if (!container.current || map.current) return;

    const instance = L.map(container.current, {
      center: [20, 80],
      zoom: 3,
      worldCopyJump: true,
    });
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
      opacity: 0.75,
      errorTileUrl: BLANK_TILE,
    }).addTo(instance);
  }, [tiles]);

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
      color: "#f97316",
      weight: 2,
      dashArray: "5 3",
      fillColor: "#f97316",
      fillOpacity: 0.08,
    }).addTo(instance);

    instance.fitBounds(bounds, { padding: [48, 48], maxZoom: 8 });
  }, [extent]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      {tiles ? (
        <div className="pointer-events-none absolute bottom-2 left-2 z-500 rounded-md bg-background/90 px-2 py-1 text-[11px] leading-tight text-muted-foreground shadow-sm backdrop-blur">
          <div className="font-medium text-foreground">{tiles.layer}</div>
          <div>imagery date {tiles.date}</div>
          {extent ? <div>bbox {extent.bbox.map((v) => v.toFixed(2)).join(", ")}</div> : null}
        </div>
      ) : (
        <div className="pointer-events-none absolute inset-x-0 top-4 z-500 flex justify-center">
          <span className="rounded-md bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
            Start a run to load flood imagery
          </span>
        </div>
      )}
    </div>
  );
}
