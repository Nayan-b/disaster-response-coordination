"use client";

/**
 * The Brief tab — the one panel a responder can act from.
 *
 * It has exactly three states and never blends them:
 *   • awaiting  — the approval gate, with the run stopped behind it;
 *   • decided   — the receipt, then the brief as it stands on the record;
 *   • otherwise — why there is nothing to decide (still running, blocked, or no event).
 */

import { Ban, CheckCircle2, FileText, Loader2, PencilLine } from "lucide-react";
import type { ReactNode } from "react";

import { ApprovalCard } from "@/components/ApprovalCard";
import { RecommendationView } from "@/components/RecommendationView";
import { EmptyState } from "@/components/ui/data-display";
import { describeOutcome, formatDateTime } from "@/lib/format";
import type {
  AllocationAction,
  ApprovalRequest,
  Claim,
  RunState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const TONE_STYLE = {
  positive: "bg-emerald-500/10 ring-emerald-500/30 text-emerald-700 dark:text-emerald-300",
  neutral: "bg-muted ring-border text-foreground",
  negative: "bg-destructive/10 ring-destructive/30 text-destructive",
} as const;

const DECISION_ICON: Record<string, ReactNode> = {
  approved: <CheckCircle2 className="size-4" />,
  edited: <PencilLine className="size-4" />,
  rejected: <Ban className="size-4" />,
};

export function BriefPanel({
  approval,
  state,
  reviewer,
  running,
  submitting,
  onDecide,
}: {
  approval: ApprovalRequest | null;
  state: RunState | null;
  reviewer: string;
  running: boolean;
  submitting: boolean;
  onDecide: (
    decision: "approved" | "edited" | "rejected",
    notes: string,
    editedActions?: AllocationAction[],
  ) => void;
}) {
  if (approval) {
    return (
      <div className="px-4 py-4">
        <ApprovalCard
          request={approval}
          reviewer={reviewer}
          submitting={submitting}
          onDecide={onDecide}
        />
      </div>
    );
  }

  const outcome = describeOutcome(state?.status);
  const decision = state?.approval;
  const recommendation = state?.recommendation;
  const verified: Claim[] = state?.fact_check?.verified_claims ?? [];

  if (!outcome && !recommendation) {
    return running ? (
      <EmptyState
        icon={<Loader2 className="size-7 animate-spin" />}
        title="Working the pipeline"
        detail="The brief appears here once the Allocation agent has drafted it and the fact-check has verified every figure it cites."
      />
    ) : (
      <EmptyState
        icon={<FileText className="size-7" />}
        title="No brief yet"
        detail="Run an event to draft a resource recommendation. Nothing reaches this panel until every number in it traces to a tool call."
      />
    );
  }

  return (
    <div className="space-y-4 px-4 py-4">
      {outcome ? (
        <div className={cn("rounded-lg px-3 py-2.5 ring-1", TONE_STYLE[outcome.tone])}>
          <div className="flex items-center gap-2">
            {decision ? (DECISION_ICON[decision.decision] ?? null) : null}
            <p className="text-xs font-semibold">{outcome.title}</p>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed opacity-90">{outcome.detail}</p>
          {decision ? (
            <p className="mt-1.5 text-[10px] opacity-75">
              {decision.reviewer} · {formatDateTime(decision.decided_at)}
              {decision.notes ? ` · “${decision.notes}”` : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      {recommendation ? (
        <RecommendationView
          recommendation={recommendation}
          verifiedClaims={verified}
          draftAttempts={state?.draft_attempts ?? null}
          llmFallbackReason={state?.unavailable?.allocation_llm ?? null}
        />
      ) : null}
    </div>
  );
}
