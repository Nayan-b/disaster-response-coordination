"use client";

/**
 * Flood Response Coordinator — the operations console.
 *
 * Three columns, one job each: pick an event on the left, see the ground truth in the middle, act on
 * the right. Across the top, the pipeline itself, so the run's position is legible at a glance
 * rather than inferred from a scrolling trace.
 *
 * The run stops at the human gate. The UI makes that stop loud — the stepper marks it, the Brief tab
 * flags it, and the tab switches itself to the approval card — because a paused run that looks
 * finished is the one failure mode this interface must not have.
 */

import { Activity, Loader2, Play, ShieldCheck, TriangleAlert } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BriefPanel } from "@/components/BriefPanel";
import { EventRail, type AlertLevel } from "@/components/EventRail";
import { EvidencePanel } from "@/components/EvidencePanel";
import { SituationPanel } from "@/components/SituationPanel";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TracePanel } from "@/components/TracePanel";
import { WorkflowStepper } from "@/components/WorkflowStepper";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TabBar, type TabDefinition } from "@/components/ui/tab-bar";
import {
  fetchEvents,
  fetchHealth,
  startRun,
  streamRun,
  submitDecision,
  type Health,
} from "@/lib/api";
import { formatElapsed } from "@/lib/format";
import type {
  AllocationAction,
  ApprovalRequest,
  FloodEvent,
  RunState,
  TraceEvent,
} from "@/lib/types";
import { deriveStages } from "@/lib/workflow";

/**
 * Map libraries must not run in the SSR pass — importing Leaflet there leaves the module reaching
 * for `window` before it exists. `ssr: false` keeps it client-side, where it works.
 */
const FloodMap = dynamic(() => import("@/components/FloodMap").then((m) => m.FloodMap), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-muted/30">
      <span className="text-xs text-muted-foreground">Loading map…</span>
    </div>
  ),
});

const REVIEWER = "responder";
// Kept in step with the backend's own defaults (`EVENTS_DEFAULT_LOOKBACK_DAYS` /
// `EVENTS_MAX_RESULTS`). A short window surfaces what is happening now rather than burying it under
// events whose window has already closed; the cap keeps triage to the most urgent handful.
const LOOKBACK_DAYS = 5;
const MAX_EVENTS = 10;
// GDACS rates the large majority of active floods Green (minor or localized), so the default stays
// on the levels that warrant coordinated allocation. The rail can widen it per session.
const DEFAULT_LEVELS: AlertLevel[] = ["Red", "Orange"];

type TabId = "brief" | "situation" | "evidence" | "trace";

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [events, setEvents] = useState<FloodEvent[]>([]);
  const [eventsMatched, setEventsMatched] = useState<number | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [levels, setLevels] = useState<AlertLevel[]>(DEFAULT_LEVELS);

  const [threadId, setThreadId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [completed, setCompleted] = useState<string[]>([]);
  const [state, setState] = useState<RunState | null>(null);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("situation");
  const [elapsed, setElapsed] = useState(0);

  const abortRef = useRef<(() => void) | null>(null);
  const startedAt = useRef<number | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
    return () => abortRef.current?.();
  }, []);

  // Depend on the joined string, not the array: an array literal changes identity every render and
  // would refetch forever. `stale` guards against a slow response for a level set the reader has
  // already changed away from landing on top of a newer one.
  const levelsKey = levels.join(",");
  useEffect(() => {
    let stale = false;
    fetchEvents(LOOKBACK_DAYS, MAX_EVENTS, levelsKey)
      .then((data) => {
        if (stale) return;
        setEvents(data.events);
        setEventsMatched(data.truncated ? (data.total_matched ?? null) : null);
        if (data.unavailable) setEventsError(data.unavailable);
      })
      .catch((err: Error) => {
        if (!stale) setEventsError(err.message);
      })
      .finally(() => {
        if (!stale) setLoadingEvents(false);
      });
    return () => {
      stale = true;
    };
  }, [levelsKey]);

  // Switching tabs — or a decision landing — puts new content in the panel, so start it at the top.
  // Without this, approving leaves the panel scrolled to where the gate's buttons were and the
  // reader lands below the receipt for the thing they just did.
  useEffect(() => {
    if (panelRef.current) panelRef.current.scrollTop = 0;
  }, [tab, state?.status]);

  // Run clock. Ticks only while a run is in flight, and freezes on the final elapsed time.
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      if (startedAt.current) setElapsed(Date.now() - startedAt.current);
    }, 500);
    return () => window.clearInterval(id);
  }, [running]);

  // Showing the skeleton is caused by the toggle, so it belongs to the handler rather than to the
  // effect that reacts to it.
  const changeLevels = useCallback((next: AlertLevel[]) => {
    setLevels(next);
    setLoadingEvents(true);
    setEventsError(null);
  }, []);

  const run = useCallback(async (event?: FloodEvent) => {
    abortRef.current?.();
    setTrace([]);
    setCompleted([]);
    setState(null);
    setApproval(null);
    setError(null);
    setFinished(false);
    setRunning(true);
    setSelectedEventId(event?.event_id ?? null);
    setTab("situation");
    startedAt.current = Date.now();
    setElapsed(0);

    try {
      const started = await startRun({
        event_id: event?.event_id ?? null,
        episode_id: event?.episode_id ?? null,
        lookback_days: LOOKBACK_DAYS,
        alert_levels: levels,
        reviewer: REVIEWER,
      });
      setThreadId(started.thread_id);

      abortRef.current = streamRun(started.thread_id, {
        onTrace: (entry) => setTrace((prev) => [...prev, entry]),
        onNodeComplete: (node) => setCompleted((prev) => [...prev, node]),
        onState: (next) => {
          setState(next);
          if (next.event) setSelectedEventId(next.event.event_id);
        },
        onApprovalRequest: (request) => {
          setApproval(request);
          // The run has stopped for a human. Put the decision in front of them rather than
          // leaving it a tab away.
          setTab("brief");
        },
        onError: (message) => setError(message),
        onDone: (info) => {
          setRunning(false);
          setFinished(true);
          // Stamp the final duration here rather than leaving it to the ticker: with a warm cache a
          // whole run lands inside one tick, and the clock would read 0:00 — or vanish entirely.
          if (startedAt.current) setElapsed(Date.now() - startedAt.current);
          if (!info.awaiting_approval) setTab("brief");
        },
      });
    } catch (err) {
      setError((err as Error).message);
      setRunning(false);
      setFinished(true);
    }
    // `levels` is read when the run starts, so a stale closure would triage the wrong level set.
  }, [levels]);

  const decide = useCallback(
    async (
      decision: "approved" | "edited" | "rejected",
      notes: string,
      editedActions?: AllocationAction[],
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
        setCompleted((prev) => [...prev, "human_gate", "action_log"]);
        // Fold the decision into the state the panels read, so the receipt and the final brief
        // render from one shape rather than from a second, parallel copy of the outcome.
        setState((prev) =>
          prev
            ? {
                ...prev,
                status: result.status,
                approval: result.approval,
                recommendation: result.recommendation ?? prev.recommendation,
              }
            : prev,
        );
        // The decision endpoint returns the run's last four trace lines, not just the new ones, so
        // the first couple are already on screen. Key on node+timestamp and append only the rest.
        setTrace((prev) => {
          const seen = new Set(prev.map((entry) => `${entry.node}@${entry.at}`));
          const fresh = (result.trace ?? []).filter(
            (entry) => !seen.has(`${entry.node}@${entry.at}`),
          );
          return fresh.length ? [...prev, ...fresh] : prev;
        });
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setSubmitting(false);
      }
    },
    [threadId],
  );

  // Per-node trace rollup for the stepper. The summary line is the *worst* line the stage produced,
  // not the last one — a stage badged amber whose caption reads like routine progress tells the
  // reader nothing about why it is amber.
  // Named `traceLevels` rather than `levels` — that name belongs to the alert-level selection above,
  // and these are trace severities, an unrelated thing.
  const { traceLevels, traceMessages } = useMemo(() => {
    const traceLevels: Record<string, "info" | "warning" | "error"> = {};
    const traceMessages: Record<string, string> = {};
    const rank = { info: 0, warning: 1, error: 2 } as const;
    for (const entry of trace) {
      const current = traceLevels[entry.node];
      if (!current || rank[entry.level] >= rank[current]) {
        traceLevels[entry.node] = entry.level;
        traceMessages[entry.node] = entry.message;
      }
    }
    return { traceLevels, traceMessages };
  }, [trace]);

  const stages = useMemo(
    () =>
      deriveStages({
        completed,
        levels: traceLevels,
        messages: traceMessages,
        running,
        awaitingApproval: Boolean(approval),
        status: state?.status ?? null,
        finished,
      }),
    [completed, traceLevels, traceMessages, running, approval, state?.status, finished],
  );

  const verifiedClaims = state?.fact_check?.verified_claims ?? approval?.verified_claims ?? [];
  const rejectedClaims = state?.fact_check?.rejected_claims ?? approval?.rejected_claims ?? [];
  const toolCalls = state?.tool_calls ?? [];
  const unavailableCount = Object.keys(state?.unavailable ?? {}).length;

  const tabs: TabDefinition<TabId>[] = [
    {
      id: "brief",
      label: "Brief",
      alert: Boolean(approval),
      title: approval ? "A decision is waiting on you" : "The drafted recommendation",
    },
    {
      id: "situation",
      label: "Situation",
      count: unavailableCount || undefined,
      countTone: "warn",
      title: unavailableCount
        ? `${unavailableCount} source(s) unavailable — nothing was estimated in their place`
        : "What the data layer found, with provenance",
    },
    {
      id: "evidence",
      label: "Evidence",
      count: toolCalls.length || undefined,
      title: "Claims and the tool calls they are checked against",
    },
    {
      id: "trace",
      label: "Trace",
      count: trace.length || undefined,
      title: "The agents' reasoning, line by line",
    },
  ];

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-background">
      {/* Header */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-2.5">
        <div className="min-w-0">
          <h1 className="flex items-center gap-1.5 text-sm leading-tight font-semibold">
            <Activity className="size-4 text-orange-500" />
            Flood Response Coordinator
          </h1>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            Live disaster data → drafted recommendation → human approval. This system records
            decisions; it never executes a response action.
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {threadId ? (
            <span
              className="hidden font-mono text-[10px] text-muted-foreground sm:inline"
              title={`Run thread ${threadId}`}
            >
              {threadId}
            </span>
          ) : null}
          {running || finished ? (
            <Badge variant="outline" className="gap-1 font-mono text-[10px] tabular-nums">
              {running ? <Loader2 className="size-3 animate-spin" /> : null}
              {formatElapsed(elapsed)}
            </Badge>
          ) : null}
          {health ? (
            <Badge
              variant="outline"
              className="gap-1 font-mono text-[10px]"
              title={
                health.llm_configured
                  ? "An LLM is configured; triage and drafting use it."
                  : "No LLM credentials — the deterministic drafting path is in use. Same evidence, same guardrails."
              }
            >
              <ShieldCheck className="size-3" />
              {health.engine}
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1 text-[10px]">
              <TriangleAlert className="size-3" />
              API unreachable
            </Badge>
          )}
          <ThemeToggle />
          <Button size="sm" onClick={() => run()} disabled={running || loadingEvents}>
            {running ? <Loader2 className="animate-spin" /> : <Play />}
            {running ? "Running…" : "Run triage"}
          </Button>
        </div>
      </header>

      {/* Pipeline */}
      <div className="border-b bg-muted/25">
        <WorkflowStepper stages={stages} />
      </div>

      {error ? (
        <div className="border-b bg-destructive/5 px-4 py-2">
          <Alert variant="destructive" className="border-0 bg-transparent p-0">
            <TriangleAlert className="size-4" />
            <AlertTitle className="text-xs">Run error</AlertTitle>
            <AlertDescription className="text-[11px]">{error}</AlertDescription>
          </Alert>
        </div>
      ) : null}

      {/* Console */}
      {/* Stacked below lg, the rows need explicit sizes — auto rows overflow the fixed-height shell
          and the tab panel gets clipped instead of scrolling. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[15rem_1fr] lg:grid-cols-[17rem_1fr_28rem] lg:grid-rows-1">
        <section className="hidden min-h-0 border-r lg:block">
          <EventRail
            events={events}
            loading={loadingEvents}
            error={eventsError}
            selectedId={selectedEventId}
            running={running}
            lookbackDays={LOOKBACK_DAYS}
            totalMatched={eventsMatched}
            levels={levels}
            onLevelsChange={changeLevels}
            onSelect={run}
          />
        </section>

        <section className="relative min-h-0 border-b lg:border-r lg:border-b-0">
          <FloodMap tiles={state?.tiles ?? null} extent={state?.extent ?? null} loading={running} />
        </section>

        <section className="flex min-h-0 flex-col">
          <div className="border-b px-3 py-2">
            <TabBar tabs={tabs} value={tab} onChange={setTab} aria-label="Run detail" />
          </div>

          <div ref={panelRef} className="min-h-0 flex-1 overflow-y-auto">
            {tab === "brief" ? (
              <BriefPanel
                approval={approval}
                state={state}
                reviewer={REVIEWER}
                running={running}
                submitting={submitting}
                onDecide={decide}
              />
            ) : tab === "situation" ? (
              <SituationPanel state={state} />
            ) : tab === "evidence" ? (
              <EvidencePanel
                verified={verifiedClaims}
                rejected={rejectedClaims}
                toolCalls={toolCalls}
                blockingReason={state?.fact_check?.blocking_reason ?? null}
                draftAttempts={state?.draft_attempts ?? null}
                supervisorOutcome={state?.supervisor_outcome ?? null}
              />
            ) : (
              <div className="h-full">
                <TracePanel trace={trace} running={running} />
              </div>
            )}
          </div>
        </section>
      </div>

      {/* The event rail has nowhere to live on a narrow screen; the triage button is the way in. */}
      <div className="border-t px-4 py-2 lg:hidden">
        <p className="text-[11px] text-muted-foreground">
          {events.length} event{events.length === 1 ? "" : "s"}
          {eventsMatched ? ` of ${eventsMatched}` : ""} in the last {LOOKBACK_DAYS} days — use{" "}
          <span className="font-medium">Run triage</span> to work the most urgent one.
        </p>
      </div>
    </main>
  );
}
