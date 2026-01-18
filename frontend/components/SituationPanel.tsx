"use client";

/**
 * What the data layer actually found, with the provenance attached to each figure.
 *
 * Two rules from the backend are load-bearing in this view:
 *  - a partial population total is labelled PARTIAL and says which country's raster is missing,
 *    because the number understates reality and a reviewer must not read it as complete;
 *  - an unavailable source is printed with its reason. Nothing renders a fallback figure.
 */

import {
  AlertTriangle,
  ExternalLink,
  Layers,
  MapPin,
  Satellite,
  Radar,
  Users,
} from "lucide-react";

import { AlertPill } from "@/components/EventRail";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EmptyState, Field, SectionTitle, Stat } from "@/components/ui/data-display";
import {
  EMPTY,
  formatArea,
  formatBbox,
  formatCompactNumber,
  formatDateTime,
  formatNumber,
  formatWindow,
  humanize,
} from "@/lib/format";
import type { RunState } from "@/lib/types";

export function SituationPanel({ state }: { state: RunState | null }) {
  if (!state?.event) {
    return (
      <EmptyState
        icon={<Radar className="size-7" />}
        title="No run yet"
        detail="Pick an event from the left, or run triage to let the Alert Monitor choose the most urgent one."
      />
    );
  }

  const { event, extent, population, imagery, tiles, unavailable } = state;
  const unavailableEntries = Object.entries(unavailable ?? {});
  const partial = Boolean(population?.is_partial);

  return (
    <div className="space-y-5 px-4 py-4">
      {/* Event header */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <AlertPill level={event.alert_level} />
          <h2 className="text-base leading-tight font-semibold">{event.country ?? event.name}</h2>
          {event.report_url ? (
            <a
              href={event.report_url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground hover:underline"
            >
              GDACS report <ExternalLink className="size-3" />
            </a>
          ) : null}
        </div>
        <dl className="divide-y rounded-lg bg-card px-3 py-1 ring-1 ring-foreground/10">
          <Field label="Event / episode" value={`${event.event_id} · ${event.episode_id ?? EMPTY}`} mono />
          <Field label="Window" value={formatWindow(event.from_date, event.to_date)} />
          <Field
            label="Countries"
            value={event.iso3_codes?.length ? event.iso3_codes.join(", ") : EMPTY}
            mono
          />
          <Field
            label="Severity"
            value={event.severity ?? EMPTY}
            title={event.severity_note ?? undefined}
          />
        </dl>
        {event.severity_note ? (
          <p className="text-[10px] leading-relaxed text-muted-foreground">{event.severity_note}</p>
        ) : null}
      </section>

      {/* Headline figures */}
      <section className="space-y-2">
        <SectionTitle>Measured</SectionTitle>
        <div className="grid grid-cols-2 gap-2">
          <Stat
            icon={<Users className="size-3" />}
            label="People in extent"
            value={
              population ? formatCompactNumber(population.population) : unavailable?.population ? "unavailable" : EMPTY
            }
            title={population ? `${formatNumber(population.population)} people` : undefined}
            tone={partial ? "warn" : population ? "default" : "muted"}
            note={
              population ? (
                <>
                  WorldPop {population.raster_year} · {population.resolution} · UN-adjusted
                  {partial ? (
                    <span className="mt-1 block font-medium text-amber-600 dark:text-amber-400">
                      PARTIAL — no raster for {population.countries_missing.join(", ")}; the true
                      figure is higher.
                    </span>
                  ) : null}
                </>
              ) : (
                (unavailable?.population ?? "not measured")
              )
            }
          />
          <Stat
            icon={<MapPin className="size-3" />}
            label="Affected area"
            value={extent ? formatArea(extent.area_sq_deg).split(" ")[0] : EMPTY}
            unit={extent ? "deg²" : undefined}
            tone={extent ? "default" : "muted"}
            note={
              extent ? (
                <>
                  {humanize(extent.geometry_type)}
                  {extent.polygon_label ? ` · ${extent.polygon_label}` : ""}
                </>
              ) : (
                (unavailable?.extent ?? "no polygon")
              )
            }
          />
          <Stat
            icon={<Satellite className="size-3" />}
            label="Imagery granules"
            value={imagery ? formatNumber(imagery.granule_count) : "0"}
            tone={imagery ? "default" : "warn"}
            note={
              imagery
                ? `${imagery.collection}${imagery.matched !== null ? ` · ${formatNumber(imagery.matched)} matched` : ""}`
                : "None in the NRT window — GIBS tiles are the fallback."
            }
          />
          <Stat
            icon={<Layers className="size-3" />}
            label="Map layer"
            value={tiles ? tiles.date : EMPTY}
            tone={tiles ? "default" : "muted"}
            note={tiles ? tiles.layer : "no tile layer"}
          />
        </div>
      </section>

      {/* Population breakdown */}
      {population ? (
        <section className="space-y-2">
          <SectionTitle>Population detail</SectionTitle>
          <dl className="divide-y rounded-lg bg-card px-3 py-1 ring-1 ring-foreground/10">
            <Field label="Total in polygon" value={`${formatNumber(population.population)} people`} mono />
            {Object.entries(population.per_country ?? {}).map(([iso3, count]) => (
              <Field key={iso3} label={iso3} value={formatNumber(count)} mono tone="muted" />
            ))}
            <Field label="Pixels summed" value={formatNumber(population.pixels_in_polygon)} mono />
            {population.nodata_pixels_in_polygon !== undefined ? (
              <Field
                label="Nodata pixels skipped"
                value={formatNumber(population.nodata_pixels_in_polygon)}
                mono
                tone="muted"
              />
            ) : null}
            {population.countries_missing?.length ? (
              <Field label="Missing rasters" value={population.countries_missing.join(", ")} tone="warn" mono />
            ) : null}
          </dl>
          {population.method ? (
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              Method: {population.method}.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* Extent detail */}
      {extent ? (
        <section className="space-y-2">
          <SectionTitle>Extent</SectionTitle>
          <dl className="divide-y rounded-lg bg-card px-3 py-1 ring-1 ring-foreground/10">
            <Field label="Bounding box" value={formatBbox(extent.bbox)} mono />
            <Field label="Geometry" value={extent.geometry_type} mono />
            <Field label="Area" value={formatArea(extent.area_sq_deg)} mono />
          </dl>
          {extent.note ? (
            <p className="text-[10px] leading-relaxed text-muted-foreground">{extent.note}.</p>
          ) : null}
        </section>
      ) : null}

      {/* Provenance */}
      <section className="space-y-2">
        <SectionTitle>Provenance</SectionTitle>
        <dl className="divide-y rounded-lg bg-card px-3 py-1 ring-1 ring-foreground/10">
          {[
            ["Extent", extent?.source, extent?.fetched_at] as const,
            ["Population", population?.source, population?.fetched_at] as const,
            ["Imagery", imagery?.source, imagery?.fetched_at] as const,
            ["Tiles", tiles?.attribution, null] as const,
          ]
            .filter(([, source]) => Boolean(source))
            .map(([label, source, fetchedAt]) => (
              <Field
                key={label}
                label={label}
                value={source ?? EMPTY}
                title={fetchedAt ? `fetched ${formatDateTime(fetchedAt)}` : undefined}
              />
            ))}
        </dl>
      </section>

      {/* Unavailable sources — always shown, never silently dropped */}
      {unavailableEntries.length > 0 ? (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" />
          <AlertTitle className="text-xs">
            {unavailableEntries.length} source
            {unavailableEntries.length === 1 ? "" : "s"} unavailable
          </AlertTitle>
          <AlertDescription>
            <ul className="mt-1 space-y-1.5">
              {unavailableEntries.map(([role, reason]) => (
                <li key={role} className="text-[11px] leading-relaxed">
                  <span className="font-medium">{humanize(role)}:</span> {reason}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] opacity-80">
              No figure was estimated or interpolated in place of these.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
