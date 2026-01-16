"use client";

/** Live agent reasoning trace, plus the tool-call log that backs every figure. */

import { useEffect, useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ToolCallSummary, TraceEvent } from "@/lib/types";

const NODE_LABELS: Record<string, string> = {
  alert_monitor: "Alert Monitor",
  imagery_retrieval: "Imagery Retrieval",
  population_overlay: "Population Overlay",
  allocation_draft: "Allocation Draft",
  fact_check: "Fact-Check",
  human_gate: "Human Gate",
  action_log: "Action Log",
};

const LEVEL_STYLES: Record<string, string> = {
  info: "text-foreground",
  warning: "text-amber-600 dark:text-amber-400",
  error: "text-red-600 dark:text-red-400",
};

export function TracePanel({
  trace,
  toolCalls,
  running,
}: {
  trace: TraceEvent[];
  toolCalls: ToolCallSummary[];
  running: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [trace.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Agent trace</h2>
        {running ? (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="size-1.5 animate-pulse rounded-full bg-emerald-500" />
            running
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">{trace.length} steps</span>
        )}
      </div>

      <ScrollArea className="flex-1 border-y">
        <ol className="divide-y">
          {trace.length === 0 ? (
            <li className="px-4 py-6 text-xs text-muted-foreground">
              No activity yet. Start a run to watch the agents work.
            </li>
          ) : (
            trace.map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="px-4 py-2.5">
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {NODE_LABELS[entry.node] ?? entry.node}
                  </span>
                  <time className="ml-auto shrink-0 text-[10px] tabular-nums text-muted-foreground">
                    {new Date(entry.at).toLocaleTimeString()}
                  </time>
                </div>
                <p className={`mt-0.5 text-xs leading-relaxed ${LEVEL_STYLES[entry.level] ?? ""}`}>
                  {entry.level === "warning" ? "⚠ " : entry.level === "error" ? "✕ " : ""}
                  {entry.message}
                </p>
              </li>
            ))
          )}
          <div ref={endRef} />
        </ol>
      </ScrollArea>

      <div className="px-4 py-3">
        <h3 className="text-xs font-semibold">
          Tool calls{" "}
          <span className="font-normal text-muted-foreground">
            — the evidence every number is checked against
          </span>
        </h3>
        <ul className="mt-2 space-y-1">
          {toolCalls.length === 0 ? (
            <li className="text-[11px] text-muted-foreground">none yet</li>
          ) : (
            toolCalls.map((call) => (
              <li key={call.call_id} className="flex items-start gap-2 text-[11px]">
                <Badge
                  variant={call.ok ? "secondary" : "destructive"}
                  className="shrink-0 font-mono text-[10px]"
                >
                  {call.call_id}
                </Badge>
                <span className="min-w-0">
                  <span className="font-mono">{call.tool}</span>
                  {call.ok ? null : (
                    <span className="text-muted-foreground"> — unavailable: {call.reason}</span>
                  )}
                </span>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
