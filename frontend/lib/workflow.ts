/**
 * The pipeline the UI draws, and how a live run maps onto it.
 *
 * The stage list mirrors `backend/app/agents/graph.py`. It is duplicated here on purpose: the
 * stepper has to show a stage as *pending* before the backend has ever mentioned it, which means
 * the frontend needs the shape of the whole pipeline up front, not just the nodes that have run.
 *
 * Status is derived from what the stream actually reports — `node_complete` frames, trace levels,
 * whether an interrupt is pending, and the run's terminal status. Nothing here guesses: a stage the
 * graph routed around is shown as skipped, not as done.
 */

export type StageStatus =
  | "pending"
  | "active"
  | "done"
  | "attention"
  | "failed"
  | "awaiting"
  | "skipped";

export interface Stage {
  id: string;
  label: string;
  /** What this stage is for, in the reviewer's language. */
  blurb: string;
}

export const STAGES: Stage[] = [
  {
    id: "alert_monitor",
    label: "Alert Monitor",
    blurb: "List live GDACS flood events and triage which one to work.",
  },
  {
    id: "imagery_retrieval",
    label: "Imagery",
    blurb: "Resolve the affected-area extent and find flood imagery for it.",
  },
  {
    id: "population_overlay",
    label: "Population",
    blurb: "Sum WorldPop UN-adjusted population inside the flood polygon.",
  },
  {
    id: "allocation_draft",
    label: "Allocation Draft",
    blurb: "Draft prioritized resource actions from the gathered evidence.",
  },
  {
    id: "fact_check",
    label: "Fact-Check",
    blurb: "Deterministic check: every number must trace to a successful tool call.",
  },
  {
    id: "human_gate",
    label: "Human Gate",
    blurb: "The run stops here until a responder approves, edits, or rejects.",
  },
  {
    id: "action_log",
    label: "Action Log",
    blurb: "Record the decision. No response action is ever executed.",
  },
];

export const STAGE_IDS = STAGES.map((stage) => stage.id);

export interface StageState extends Stage {
  status: StageStatus;
  /** Trace lines this stage produced, newest last. */
  detail: string | null;
}

export interface DeriveInput {
  /** Node ids in the order `node_complete` reported them. */
  completed: string[];
  /** Worst trace level seen per node. */
  levels: Record<string, "info" | "warning" | "error">;
  /** Last trace message per node — the one-line summary shown under the stage. */
  messages: Record<string, string>;
  running: boolean;
  awaitingApproval: boolean;
  status: string | null;
  /** True once the run has stopped for good (finished, blocked, or errored out). */
  finished: boolean;
}

export function deriveStages(input: DeriveInput): StageState[] {
  const completed = new Set(input.completed);
  const lastCompletedIndex = STAGE_IDS.reduce(
    (last, id, index) => (completed.has(id) ? index : last),
    -1,
  );

  return STAGES.map((stage, index) => {
    const detail = input.messages[stage.id] ?? null;
    const level = input.levels[stage.id];

    if (completed.has(stage.id)) {
      const status: StageStatus =
        level === "error" ? "failed" : level === "warning" ? "attention" : "done";
      return { ...stage, status, detail };
    }

    if (stage.id === "human_gate" && input.awaitingApproval) {
      return { ...stage, status: "awaiting", detail };
    }

    if (input.running && index === lastCompletedIndex + 1) {
      return { ...stage, status: "active", detail };
    }

    // The run is over and this stage never ran — the graph routed around it (no event, no extent,
    // or blocked by the fact-check). Saying "skipped" is honest; leaving it grey reads as "pending".
    if (input.finished && !input.awaitingApproval) {
      return { ...stage, status: "skipped", detail };
    }

    return { ...stage, status: "pending", detail };
  });
}

/** Tailwind classes per status, kept in one place so the stepper and the trace agree. */
export const STATUS_STYLE: Record<
  StageStatus,
  { dot: string; text: string; ring: string; label: string }
> = {
  pending: {
    dot: "bg-muted-foreground/25",
    text: "text-muted-foreground",
    ring: "ring-border",
    label: "pending",
  },
  active: {
    dot: "bg-sky-500 animate-pulse",
    text: "text-sky-700 dark:text-sky-300",
    ring: "ring-sky-500/40 bg-sky-500/5",
    label: "running",
  },
  done: {
    dot: "bg-emerald-500",
    text: "text-foreground",
    ring: "ring-border",
    label: "done",
  },
  attention: {
    dot: "bg-amber-500",
    text: "text-amber-700 dark:text-amber-300",
    ring: "ring-amber-500/35",
    label: "with warnings",
  },
  failed: {
    dot: "bg-red-500",
    text: "text-red-700 dark:text-red-300",
    ring: "ring-red-500/35",
    label: "failed",
  },
  awaiting: {
    dot: "bg-amber-500 animate-pulse",
    text: "text-amber-700 dark:text-amber-300",
    ring: "ring-amber-500/50 bg-amber-500/5",
    label: "your call",
  },
  skipped: {
    dot: "bg-muted-foreground/20",
    text: "text-muted-foreground/60",
    ring: "ring-border/60",
    label: "skipped",
  },
};
