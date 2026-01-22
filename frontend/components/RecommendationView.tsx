"use client";

/**
 * The drafted recommendation, rendered read-only.
 *
 * Shared by the approval gate and the post-decision view so a responder reads the same brief before
 * and after deciding. Every action lists the claims that back it, and each claim is shown with the
 * tool call it was verified against — the citation is the point, not decoration.
 */

import { CheckCircle2, CircleAlert, Cpu, Info, RefreshCw, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { SectionTitle } from "@/components/ui/data-display";
import { formatDateTime, formatMeasure } from "@/lib/format";
import type { AllocationAction, Claim, Recommendation } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Says which engine drafted this — and, when it is the rule-based one, *why*.
 *
 * Those are two different situations and conflating them is actively misleading: "no credentials
 * configured" and "the configured model was tried and failed" look identical in the output but call
 * for completely different responses from the reader. `fallbackReason` is
 * `unavailable.allocation_llm`, which the drafter records when a configured provider errors.
 */
export function EngineBadge({
  draftedBy,
  fallbackReason,
}: {
  draftedBy: string;
  fallbackReason?: string | null;
}) {
  const ruleBased = draftedBy === "rule-based";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge variant="outline" className="gap-1 font-mono text-[10px]">
        <Cpu className="size-3" />
        {draftedBy}
      </Badge>
      {ruleBased && fallbackReason ? (
        <Badge
          variant="outline"
          className="gap-1 border-amber-500/40 text-[10px] text-amber-700 dark:text-amber-400"
          title={`The configured LLM was attempted and failed, so the deterministic path drafted this instead.\n\n${fallbackReason}`}
        >
          <TriangleAlert className="size-3" />
          LLM failed — fell back
        </Badge>
      ) : ruleBased ? (
        <span
          className="text-[10px] text-muted-foreground"
          title="No LLM credentials are configured, so the deterministic drafting path produced this. The evidence and the guardrails are identical."
        >
          deterministic draft
        </span>
      ) : null}
    </span>
  );
}

export function ClaimLine({ claim, id }: { claim: Claim | undefined; id: string }) {
  if (!claim) {
    return (
      <li className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
        <CircleAlert className="mt-px size-3 shrink-0" />
        <span className="font-mono">{id}</span>
        <span>— claim not in this run&apos;s verified set</span>
      </li>
    );
  }
  return (
    <li className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
      <CheckCircle2 className="mt-px size-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
      <span className="min-w-0">
        <span className="text-foreground/80">{claim.text}</span>
        {claim.value !== null ? (
          <span className="ml-1 font-medium text-foreground tabular-nums">
            ({formatMeasure(claim.value, claim.unit)})
          </span>
        ) : null}
        {claim.source_call_id ? (
          <span
            className="ml-1.5 rounded bg-muted px-1 py-px font-mono text-[10px]"
            title="The tool call this figure was verified against"
          >
            {claim.source_call_id}
          </span>
        ) : null}
      </span>
    </li>
  );
}

export function ActionList({
  actions,
  claimById,
}: {
  actions: AllocationAction[];
  claimById: Map<string, Claim>;
}) {
  if (actions.length === 0) {
    return (
      <p className="rounded-lg bg-muted/50 px-3 py-2.5 text-xs text-muted-foreground">
        No actions survived verification. Every drafted action cited a figure that could not be
        traced to a successful tool call in this run.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {actions.map((action) => (
        <li
          key={`${action.priority}-${action.action}`}
          className="rounded-lg bg-card p-3 ring-1 ring-foreground/10"
        >
          <div className="flex gap-2.5">
            <span
              className={cn(
                "mt-px flex size-5 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold tabular-nums",
                action.priority === 1
                  ? "bg-foreground text-background"
                  : "bg-muted text-muted-foreground",
              )}
              title={`Priority ${action.priority}`}
            >
              {action.priority}
            </span>
            <div className="min-w-0 flex-1 space-y-1.5">
              <p className="text-xs leading-snug font-medium">{action.action}</p>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {action.rationale}
              </p>
              {action.supporting_claim_ids.length > 0 ? (
                <ul className="space-y-1 border-l-2 border-emerald-500/30 pl-2">
                  {action.supporting_claim_ids.map((id) => (
                    <ClaimLine key={id} id={id} claim={claimById.get(id)} />
                  ))}
                </ul>
              ) : (
                <p className="text-[10px] text-muted-foreground/70 italic">
                  No numeric claim — nothing to verify.
                </p>
              )}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

/**
 * Shown when the supervisor sent a blocked draft back. A reviewer should know this brief is the
 * product of a correction rather than a first attempt — the trace has the detail.
 */
export function RepairBadge({ attempts }: { attempts: number }) {
  return (
    <Badge
      variant="outline"
      className="gap-1 text-[10px]"
      title={`The fact-check blocked an earlier draft. The supervisor sent it back with the rejection reasons; this is draft ${attempts}.`}
    >
      <RefreshCw className="size-3" />
      redrafted · attempt {attempts}
    </Badge>
  );
}

export function RecommendationView({
  recommendation,
  verifiedClaims,
  draftAttempts,
  llmFallbackReason,
  showHeader = true,
}: {
  recommendation: Recommendation;
  verifiedClaims: Claim[];
  draftAttempts?: number | null;
  llmFallbackReason?: string | null;
  showHeader?: boolean;
}) {
  const claimById = new Map(verifiedClaims.map((claim) => [claim.claim_id, claim]));

  return (
    <div className="space-y-4">
      {showHeader ? (
        <header className="space-y-1.5">
          <h2 className="text-sm leading-snug font-semibold text-balance">
            {recommendation.headline}
          </h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {recommendation.situation}
          </p>
          <div className="flex flex-wrap items-center gap-2 pt-0.5 text-[10px] text-muted-foreground">
            <EngineBadge
              draftedBy={recommendation.drafted_by}
              fallbackReason={llmFallbackReason}
            />
            {draftAttempts && draftAttempts > 1 ? <RepairBadge attempts={draftAttempts} /> : null}
            <span>drafted {formatDateTime(recommendation.drafted_at)}</span>
          </div>
        </header>
      ) : null}

      <section className="space-y-2">
        <SectionTitle>
          Recommended actions
          <span className="ml-1.5 font-normal normal-case">
            ({recommendation.actions.length}, highest priority first)
          </span>
        </SectionTitle>
        <ActionList actions={recommendation.actions} claimById={claimById} />
      </section>

      {recommendation.caveats.length > 0 ? (
        <section className="space-y-2">
          <SectionTitle>Caveats</SectionTitle>
          <ul className="space-y-1.5 rounded-lg bg-muted/50 px-3 py-2.5">
            {recommendation.caveats.map((caveat) => (
              <li
                key={caveat}
                className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground"
              >
                <Info className="mt-px size-3 shrink-0 opacity-60" />
                <span>{caveat}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
