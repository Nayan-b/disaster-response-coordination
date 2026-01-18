"use client";

/**
 * The pipeline, drawn as it runs.
 *
 * This is the piece that was missing: the stream already emitted a `node_complete` frame for every
 * graph node and nothing consumed it, so a run was a wall of trace text with no sense of where it
 * was. Each stage shows its live status, its last trace line, and — for the human gate — that the
 * run has *stopped* and is waiting on the reader.
 */

import {
  AlertTriangle,
  Check,
  CircleDashed,
  Hand,
  Minus,
  Satellite,
  ScrollText,
  ShieldCheck,
  Siren,
  Users,
  X,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { STATUS_STYLE, type StageState, type StageStatus } from "@/lib/workflow";

const STAGE_ICON: Record<string, ReactNode> = {
  alert_monitor: <Siren className="size-3.5" />,
  imagery_retrieval: <Satellite className="size-3.5" />,
  population_overlay: <Users className="size-3.5" />,
  allocation_draft: <ScrollText className="size-3.5" />,
  fact_check: <ShieldCheck className="size-3.5" />,
  human_gate: <Hand className="size-3.5" />,
  action_log: <ScrollText className="size-3.5" />,
};

function StatusMark({ status }: { status: StageStatus }) {
  const base = "flex size-5 shrink-0 items-center justify-center rounded-full";
  switch (status) {
    case "done":
      return (
        <span className={cn(base, "bg-emerald-500 text-white")}>
          <Check className="size-3" strokeWidth={3} />
        </span>
      );
    case "attention":
      return (
        <span className={cn(base, "bg-amber-500 text-amber-950")}>
          <AlertTriangle className="size-3" strokeWidth={3} />
        </span>
      );
    case "failed":
      return (
        <span className={cn(base, "bg-red-500 text-white")}>
          <X className="size-3" strokeWidth={3} />
        </span>
      );
    case "active":
      return (
        <span className={cn(base, "bg-sky-500/15 text-sky-600 dark:text-sky-400")}>
          <span className="size-2 animate-pulse rounded-full bg-sky-500" />
        </span>
      );
    case "awaiting":
      return (
        <span className={cn(base, "animate-pulse bg-amber-500 text-amber-950")}>
          <Hand className="size-3" strokeWidth={2.5} />
        </span>
      );
    case "skipped":
      return (
        <span className={cn(base, "bg-muted text-muted-foreground/60")}>
          <Minus className="size-3" strokeWidth={3} />
        </span>
      );
    default:
      return (
        <span className={cn(base, "text-muted-foreground/40")}>
          <CircleDashed className="size-4" />
        </span>
      );
  }
}

export function WorkflowStepper({ stages }: { stages: StageState[] }) {
  return (
    <nav aria-label="Pipeline progress" className="w-full overflow-x-auto">
      <ol className="flex min-w-max items-stretch gap-1.5 px-4 py-2.5 lg:min-w-0">
        {stages.map((stage, index) => {
          const style = STATUS_STYLE[stage.status];
          const isLast = index === stages.length - 1;
          return (
            <li key={stage.id} className="flex min-w-0 flex-1 items-center gap-1.5">
              <div
                title={stage.detail ? `${stage.blurb}\n\n${stage.detail}` : stage.blurb}
                className={cn(
                  "flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-1.5 ring-1 transition-colors",
                  style.ring,
                )}
              >
                <StatusMark status={stage.status} />
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "flex items-center gap-1.5 text-xs leading-tight font-medium",
                      style.text,
                    )}
                  >
                    <span className="shrink-0 opacity-60">{STAGE_ICON[stage.id]}</span>
                    <span className="truncate">{stage.label}</span>
                  </span>
                  <span className="block truncate text-[10px] leading-tight text-muted-foreground">
                    {stage.status === "pending" || stage.status === "skipped"
                      ? style.label
                      : (stage.detail ?? style.label)}
                  </span>
                </span>
              </div>
              {!isLast ? (
                <span
                  aria-hidden
                  className={cn(
                    "h-px w-3 shrink-0",
                    stage.status === "done" ||
                      stage.status === "attention" ||
                      stage.status === "failed"
                      ? "bg-foreground/25"
                      : "bg-border",
                  )}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
