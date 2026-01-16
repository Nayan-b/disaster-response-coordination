"use client";

/**
 * Flood Response Coordinator — map-first dashboard.
 *
 * Layout: map on the left, agent trace and the approval card on the right. The trace streams while
 * the graph runs; when it interrupts, the approval card appears and nothing proceeds until the
 * responder decides.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApprovalCard } from "@/components/ApprovalCard";
import { TracePanel } from "@/components/TracePanel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  fetchEvents,
  fetchHealth,
  startRun,
  streamRun,
  submitDecision,
  type Health,
} from "@/lib/api";
import type {
  ApprovalRequest,
  FloodEvent,
  RunState,
  ToolCallSummary,
  TraceEvent,
} from "@/lib/types";

/**
 * MapLibre must load in the browser only. Importing it during the SSR pass leaves its web worker
 * uninitialized, and the map then renders an empty canvas and never requests a single tile — no
 * error, just a blank map. `ssr: false` keeps the module client-side, where it works.
 */
const FloodMap = dynamic(() => import("@/components/FloodMap").then((m) => m.FloodMap), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-muted/30">
      <span className="text-xs text-muted-foreground">Loading map…</span>
    </div>
  ),
});

const ALERT_COLORS: Record<string, string> = {
  Red: "bg-red-600 text-white hover:bg-red-600",
  Orange: "bg-orange-500 text-orange-950 hover:bg-orange-500",
  Green: "bg-emerald-600 text-white hover:bg-emerald-600",
};

const REVIEWER = "responder";

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [events, setEvents] = useState<FloodEvent[]>([]);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [loadingEvents, setLoadingEvents] = useState(true);

  const [threadId, setThreadId] = useState<string | null>(null);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallSummary[]>([]);
  const [state, setState] = useState<RunState | null>(null);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [running, setRunning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const abortRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
    fetchEvents(45)
      .then((data) => {
        setEvents(data.events);
        if (data.unavailable) setEventsError(data.unavailable);
      })
      .catch((err: Error) => setEventsError(err.message))
      .finally(() => setLoadingEvents(false));
    return () => abortRef.current?.();
  }, []);

  const run = useCallback(async (eventId?: number, episodeId?: number | null) => {
    abortRef.current?.();
    setTrace([]);
    setToolCalls([]);
    setState(null);
    setApproval(null);
    setError(null);
    setOutcome(null);
    setRunning(true);

    try {
      const started = await startRun({
        event_id: eventId ?? null,
        episode_id: episodeId ?? null,
        lookback_days: 45,
        reviewer: REVIEWER,
      });
      setThreadId(started.thread_id);

      abortRef.current = streamRun(started.thread_id, {
        onTrace: (entry) => setTrace((prev) => [...prev, entry]),
        onState: (next) => {
          setState(next);
          setToolCalls(next.tool_calls ?? []);
        },
        onApprovalRequest: setApproval,
        onError: (message) => setError(message),
        onDone: (info) => {
          setRunning(false);
          if (!info.awaiting_approval && info.status) setOutcome(info.status);
        },
      });
    } catch (err) {
      setError((err as Error).message);
      setRunning(false);
    }
  }, []);

  const decide = useCallback(
    async (
      decision: "approved" | "edited" | "rejected",
      notes: string,
      editedActions?: unknown[],
    ) => {
      if (!threadId) return;
      setSubmitting(true);
      setError(null);
      try {
        const result = await submitDecision(threadId, {
          decision,
          reviewer: REVIEWER,
          notes: notes || null,
          edited_actions: editedActions ?? null,
        });
        setApproval(null);
        setOutcome(result.status);
        setTrace((prev) => [
          ...prev,
          {
            node: "human_gate",
            message: `You ${result.approval.decision} this recommendation${notes ? `: ${notes}` : ""}`,
            at: new Date().toISOString(),
            level: "info",
          },
        ]);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setSubmitting(false);
      }
    },
    [threadId],
  );

  const population = state?.population;

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-background">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-3">
        <div>
          <h1 className="text-sm font-semibold leading-tight">Flood Response Coordinator</h1>
          <p className="text-[11px] text-muted-foreground">
            Live disaster data → recommendation → human approval
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {health ? (
            <Badge variant="outline" className="font-mono text-[10px]">
              engine {health.engine}
            </Badge>
          ) : (
            <Badge variant="destructive" className="text-[10px]">
              API unreachable
            </Badge>
          )}
          <Button size="sm" onClick={() => run()} disabled={running || loadingEvents}>
            {running ? "Running…" : "Run triage"}
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_29rem]">
        {/* Map */}
        <section className="relative min-h-[18rem] border-b lg:border-b-0 lg:border-r">
          <FloodMap tiles={state?.tiles ?? null} extent={state?.extent ?? null} />
        </section>

        {/* Side panel */}
        <section className="flex min-h-0 flex-col overflow-y-auto">
          {/* Event picker */}
          <div className="border-b px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Active flood events
              <span className="ml-1 font-normal normal-case">(GDACS, last 45 days)</span>
            </h2>
            {loadingEvents ? (
              <p className="mt-2 text-xs text-muted-foreground">Loading events…</p>
            ) : eventsError ? (
              <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{eventsError}</p>
            ) : events.length === 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                GDACS reports no Orange/Red flood events in this window. That is a real result.
              </p>
            ) : (
              <ul className="mt-2 space-y-1.5">
                {events.map((event) => (
                  <li key={`${event.event_id}-${event.episode_id}`}>
                    <button
                      type="button"
                      onClick={() => run(event.event_id, event.episode_id)}
                      disabled={running}
                      className="flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition-colors hover:bg-accent disabled:opacity-50"
                    >
                      <Badge
                        className={`shrink-0 text-[10px] ${ALERT_COLORS[event.alert_level ?? ""] ?? ""}`}
                      >
                        {event.alert_level ?? "—"}
                      </Badge>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">
                          {event.country ?? event.name}
                        </span>
                        <span className="block truncate text-[10px] text-muted-foreground">
                          event {event.event_id} · {event.from_date} → {event.to_date}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Findings */}
          {state?.event ? (
            <div className="grid grid-cols-2 gap-2 border-b px-4 py-3">
              <Metric
                label="People in extent"
                value={
                  population
                    ? population.population.toLocaleString()
                    : state.unavailable?.population
                      ? "unavailable"
                      : "—"
                }
                note={
                  population
                    ? `WorldPop ${population.raster_year} · ${population.resolution} · UN-adjusted${
                        population.is_partial ? " · PARTIAL" : ""
                      }`
                    : state.unavailable?.population
                }
                warn={Boolean(population?.is_partial) || Boolean(state.unavailable?.population)}
              />
              <Metric
                label="Imagery granules"
                value={
                  state.imagery
                    ? String(state.imagery.granule_count)
                    : state.unavailable?.imagery
                      ? "none in window"
                      : "—"
                }
                note={state.imagery?.collection ?? "GIBS tiles used as fallback"}
                warn={Boolean(state.unavailable?.imagery)}
              />
            </div>
          ) : null}

          {/* Approval / outcome */}
          <div className="space-y-3 px-4 py-3">
            {error ? (
              <Alert variant="destructive">
                <AlertTitle className="text-xs">Run error</AlertTitle>
                <AlertDescription className="text-[11px]">{error}</AlertDescription>
              </Alert>
            ) : null}

            {approval ? (
              <ApprovalCard
                request={approval}
                reviewer={REVIEWER}
                submitting={submitting}
                onDecide={decide}
              />
            ) : null}

            {outcome && !approval ? (
              <Card>
                <CardContent className="py-3">
                  <p className="text-xs">
                    Run finished:{" "}
                    <span className="font-mono font-medium">{outcome.replace("logged_", "")}</span>
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Recorded in the action log. No response action was executed by this system.
                  </p>
                </CardContent>
              </Card>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 border-t">
            <TracePanel trace={trace} toolCalls={toolCalls} running={running} />
          </div>
        </section>
      </div>
    </main>
  );
}

function Metric({
  label,
  value,
  note,
  warn,
}: {
  label: string;
  value: string;
  note?: string | null;
  warn?: boolean;
}) {
  return (
    <div className="rounded-md border px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={`mt-0.5 text-lg font-semibold tabular-nums leading-tight ${
          warn ? "text-amber-600 dark:text-amber-400" : ""
        }`}
      >
        {value}
      </div>
      {note ? (
        <div className="mt-0.5 text-[10px] leading-tight text-muted-foreground">{note}</div>
      ) : null}
    </div>
  );
}
