"use client";

/**
 * The live reasoning trace.
 *
 * Grouped by stage rather than shown as one flat river of text: a run emits a dozen-plus lines and
 * "which agent said this" is the first thing a reader needs. Warnings and errors can be isolated,
 * because on a run that went sideways those are the only lines that matter.
 *
 * Auto-scroll follows the tail only while the reader is already at the bottom — yanking the view
 * back down while someone is reading an earlier line is worse than not following at all.
 */

import { AlertTriangle, ListFilter, Radio, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { EmptyState } from "@/components/ui/data-display";
import { formatClock, humanize } from "@/lib/format";
import type { TraceEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { STAGES } from "@/lib/workflow";

const STAGE_LABEL = new Map(STAGES.map((stage) => [stage.id, stage.label]));

const LEVEL_STYLE: Record<string, string> = {
  info: "text-foreground/85",
  warning: "text-amber-700 dark:text-amber-300",
  error: "text-red-700 dark:text-red-300",
};

export function TracePanel({
  trace,
  running,
}: {
  trace: TraceEvent[];
  running: boolean;
}) {
  const [onlyIssues, setOnlyIssues] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  const issues = trace.filter((entry) => entry.level !== "info").length;
  const visible = onlyIssues ? trace.filter((entry) => entry.level !== "info") : trace;

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !pinnedToBottom.current) return;
    node.scrollTop = node.scrollHeight;
  }, [visible.length]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-2">
        <span className="text-xs font-semibold">Reasoning trace</span>
        {running ? (
          <span className="flex items-center gap-1.5 text-[11px] text-sky-600 dark:text-sky-400">
            <Radio className="size-3 animate-pulse" />
            live
          </span>
        ) : (
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {trace.length} line{trace.length === 1 ? "" : "s"}
          </span>
        )}
        {issues > 0 ? (
          <button
            type="button"
            onClick={() => setOnlyIssues((prev) => !prev)}
            aria-pressed={onlyIssues}
            className={cn(
              "ml-auto flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium transition-colors",
              onlyIssues
                ? "bg-amber-500/20 text-amber-700 dark:text-amber-300"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            <ListFilter className="size-3" />
            {issues} issue{issues === 1 ? "" : "s"}
          </button>
        ) : null}
      </div>

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const el = event.currentTarget;
          pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
        }}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {visible.length === 0 ? (
          <EmptyState
            title={onlyIssues ? "No warnings or errors" : "Nothing yet"}
            detail={
              onlyIssues
                ? "Every line in this run is informational."
                : "Start a run to watch the agents work through the pipeline."
            }
          />
        ) : (
          <ol className="divide-y">
            {visible.map((entry, index) => {
              const previous = visible[index - 1];
              const newStage = previous?.node !== entry.node;
              return (
                <li key={`${entry.at}-${index}`} className="px-4 py-2">
                  {newStage ? (
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                        {STAGE_LABEL.get(entry.node) ?? humanize(entry.node)}
                      </span>
                      <span className="h-px flex-1 bg-border" />
                    </div>
                  ) : null}
                  <div className="flex items-start gap-2">
                    {entry.level === "warning" ? (
                      <AlertTriangle className="mt-0.5 size-3 shrink-0 text-amber-500" />
                    ) : entry.level === "error" ? (
                      <XCircle className="mt-0.5 size-3 shrink-0 text-red-500" />
                    ) : (
                      <span className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground/40" />
                    )}
                    <p
                      className={cn(
                        "min-w-0 flex-1 text-xs leading-relaxed break-words",
                        LEVEL_STYLE[entry.level] ?? "",
                      )}
                    >
                      {entry.message}
                    </p>
                    <time
                      dateTime={entry.at}
                      className="shrink-0 text-[10px] text-muted-foreground tabular-nums"
                    >
                      {formatClock(entry.at)}
                    </time>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
