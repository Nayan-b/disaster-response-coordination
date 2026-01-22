"use client";

/**
 * The event picker.
 *
 * A responder's first question is "which of these is worst, and is it still active" — so each row
 * leads with the alert level, then the country, then the date window, and rows are ordered by
 * severity. GDACS reporting zero events is a real answer and is written as one, not as an error.
 */

import { CalendarRange, ExternalLink, Loader2, Search, WifiOff } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/data-display";
import { Skeleton } from "@/components/ui/skeleton";
import { formatWindow } from "@/lib/format";
import type { FloodEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const ALERT_STYLE: Record<string, string> = {
  Red: "bg-red-600 text-white",
  Orange: "bg-orange-500 text-orange-950",
  Green: "bg-emerald-600 text-white",
};

const ALERT_RANK: Record<string, number> = { Red: 3, Orange: 2, Green: 1 };

export type AlertLevel = "Red" | "Orange" | "Green";

/** Most severe first — the same order the backend ranks by. */
export const ALERT_LEVELS: AlertLevel[] = ["Red", "Orange", "Green"];

/** What each level means in GDACS's own terms, so the toggle isn't a mystery switch. */
const LEVEL_HINT: Record<AlertLevel, string> = {
  Red: "GDACS Red — severe, high likelihood of significant impact",
  Orange: "GDACS Orange — moderate, may need international coordination",
  Green: "GDACS Green — minor or localized. Most active floods sit here, so turning this on widens the list a lot",
};

const LEVEL_ON: Record<AlertLevel, string> = {
  Red: "bg-red-600 text-white ring-red-600",
  Orange: "bg-orange-500 text-orange-950 ring-orange-500",
  Green: "bg-emerald-600 text-white ring-emerald-600",
};

export function AlertPill({ level, className }: { level: string | null; className?: string }) {
  return (
    <Badge
      className={cn(
        "shrink-0 text-[10px] font-semibold tracking-wide uppercase",
        ALERT_STYLE[level ?? ""] ?? "bg-muted text-muted-foreground",
        className,
      )}
    >
      {level ?? "unrated"}
    </Badge>
  );
}

export function EventRail({
  events,
  loading,
  error,
  selectedId,
  running,
  lookbackDays,
  totalMatched,
  levels,
  onLevelsChange,
  onSelect,
}: {
  events: FloodEvent[];
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  running: boolean;
  lookbackDays: number;
  /** Set only when the backend capped the list — how many matched before the cut. */
  totalMatched: number | null;
  levels: AlertLevel[];
  onLevelsChange: (next: AlertLevel[]) => void;
  onSelect: (event: FloodEvent) => void;
}) {
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return events
      .filter((event) =>
        needle
          ? `${event.country ?? ""} ${event.name} ${event.event_id}`.toLowerCase().includes(needle)
          : true,
      )
      .slice()
      .sort(
        (a, b) =>
          (ALERT_RANK[b.alert_level ?? ""] ?? 0) - (ALERT_RANK[a.alert_level ?? ""] ?? 0) ||
          (b.to_date ?? "").localeCompare(a.to_date ?? ""),
      );
  }, [events, query]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2.5 border-b px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Active flood events</h2>
          <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
            <CalendarRange className="size-3" />
            GDACS · last {lookbackDays} days
          </p>
          <div className="mt-2 flex items-center gap-1" role="group" aria-label="Alert levels">
            {ALERT_LEVELS.map((level) => {
              const on = levels.includes(level);
              // Never allow an empty selection: the backend treats "no levels" as its Orange+Red
              // default, so an empty picker would silently show a filtered list anyway.
              const isLastOn = on && levels.length === 1;
              return (
                <button
                  key={level}
                  type="button"
                  aria-pressed={on}
                  disabled={isLastOn || running}
                  title={isLastOn ? "Keep at least one level selected" : LEVEL_HINT[level]}
                  onClick={() =>
                    onLevelsChange(
                      on ? levels.filter((l) => l !== level) : [...levels, level],
                    )
                  }
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase ring-1 transition-colors",
                    "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                    on
                      ? LEVEL_ON[level]
                      : "bg-transparent text-muted-foreground ring-border hover:text-foreground",
                    (isLastOn || running) && "cursor-not-allowed opacity-70",
                  )}
                >
                  {level}
                </button>
              );
            })}
          </div>
          {/* A capped list must say so, or ten rows read as "these are all of them". */}
          {totalMatched ? (
            <p
              className="mt-1 text-[10px] text-amber-600 dark:text-amber-400"
              title={`${totalMatched} events matched the window; showing the most urgent ${events.length}, ranked by alert level then recency.`}
            >
              Showing the {events.length} most urgent of {totalMatched} matched
            </p>
          ) : null}
        </div>
        {events.length > 4 ? (
          <div className="relative">
            <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by country or id"
              aria-label="Filter events"
              className="w-full rounded-md bg-muted/60 py-1.5 pr-2.5 pl-8 text-xs ring-1 ring-transparent transition-shadow outline-none placeholder:text-muted-foreground focus:bg-background focus:ring-ring/50"
            />
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <ul className="space-y-2 p-1">
            {[0, 1, 2].map((i) => (
              <li key={i} className="space-y-2 rounded-lg p-2 ring-1 ring-border">
                <Skeleton className="h-3.5 w-16" />
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-2.5 w-24" />
              </li>
            ))}
          </ul>
        ) : error ? (
          <EmptyState
            icon={<WifiOff className="size-6" />}
            title="Event list unavailable"
            detail={error}
          />
        ) : events.length === 0 ? (
          <EmptyState
            title="No events at these levels"
            detail={`GDACS reports no ${levels.join(" or ")} flood events in the last ${lookbackDays} days. That is a real result, not a failure — nothing was substituted in its place.${
              levels.includes("Green") ? "" : " Most active floods are rated Green; add that level to widen the list."
            }`}
          />
        ) : visible.length === 0 ? (
          <EmptyState title="No match" detail={`Nothing matches “${query}”.`} />
        ) : (
          <ul className="space-y-1.5">
            {visible.map((event) => {
              const selected = event.event_id === selectedId;
              return (
                <li key={`${event.event_id}-${event.episode_id}`}>
                  <button
                    type="button"
                    onClick={() => onSelect(event)}
                    disabled={running}
                    aria-current={selected ? "true" : undefined}
                    className={cn(
                      "group flex w-full flex-col gap-1.5 rounded-lg px-2.5 py-2 text-left ring-1 transition-colors",
                      "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                      selected
                        ? "bg-accent ring-foreground/25"
                        : "ring-border hover:bg-accent/60 hover:ring-foreground/15",
                      running && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <AlertPill level={event.alert_level} />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium">
                        {event.country ?? event.name}
                      </span>
                      {selected && running ? (
                        <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
                      ) : null}
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                      <span className="font-mono">#{event.event_id}</span>
                      <span aria-hidden>·</span>
                      <span className="truncate">
                        {formatWindow(event.from_date, event.to_date)}
                      </span>
                      {event.report_url ? (
                        <a
                          href={event.report_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="ml-auto shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                          aria-label={`Open the GDACS report for event ${event.event_id}`}
                        >
                          <ExternalLink className="size-3" />
                        </a>
                      ) : null}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <p className="border-t px-4 py-2.5 text-[10px] leading-relaxed text-muted-foreground">
        Selecting an event starts a run. The pipeline stops at the human gate — nothing is dispatched
        without your decision.
      </p>
    </div>
  );
}
