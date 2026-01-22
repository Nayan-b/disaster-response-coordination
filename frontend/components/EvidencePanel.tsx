"use client";

/**
 * The audit trail: every claim and the tool call behind it.
 *
 * This panel is the visible half of the project's central rule — a number that cannot be traced to
 * a successful tool call in *this run* does not get to appear in a recommendation. So both sides are
 * shown: what passed, and what the fact-check threw out and why.
 */

import { CheckCircle2, ChevronRight, Database, XCircle } from "lucide-react";
import { useState } from "react";

import { EmptyState, SectionTitle } from "@/components/ui/data-display";
import { Badge } from "@/components/ui/badge";
import { EMPTY, formatClock, formatMeasure } from "@/lib/format";
import type { Claim, ToolCallSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const SUPERVISOR_OUTCOME: Record<string, string> = {
  retry: "The supervisor sent the draft back to be redrafted.",
  stagnant:
    "The redraft failed on exactly the same claims as the attempt before it, so the supervisor stopped rather than spending the remaining attempts proving it.",
  exhausted:
    "The supervisor used every redraft attempt and the figures still could not be verified.",
};

export function EvidencePanel({
  verified,
  rejected,
  toolCalls,
  blockingReason,
  draftAttempts,
  supervisorOutcome,
}: {
  verified: Claim[];
  rejected: Claim[];
  toolCalls: ToolCallSummary[];
  blockingReason: string | null;
  draftAttempts: number | null;
  supervisorOutcome: string | null;
}) {
  if (toolCalls.length === 0 && verified.length === 0 && rejected.length === 0) {
    return (
      <EmptyState
        icon={<Database className="size-7" />}
        title="No evidence yet"
        detail="Every figure in a recommendation is checked against the tool calls listed here. Start a run to populate it."
      />
    );
  }

  return (
    <div className="space-y-5 px-4 py-4">
      {blockingReason ? (
        <div className="rounded-lg bg-destructive/10 px-3 py-2.5 ring-1 ring-destructive/30">
          <p className="text-xs font-medium text-destructive">Blocked by the fact-check</p>
          <p className="mt-1 text-[11px] leading-relaxed text-destructive/90">{blockingReason}</p>
          {supervisorOutcome && SUPERVISOR_OUTCOME[supervisorOutcome] ? (
            <p className="mt-1.5 text-[11px] leading-relaxed text-destructive/90">
              {draftAttempts && draftAttempts > 1
                ? `${draftAttempts} drafts were attempted. `
                : ""}
              {SUPERVISOR_OUTCOME[supervisorOutcome]}
            </p>
          ) : null}
          <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
            A draft whose load-bearing figure fails verification never reaches a human — a
            recommendation with a hole in it is worse than no recommendation.
          </p>
        </div>
      ) : null}

      <section className="space-y-2">
        <SectionTitle
          action={
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {verified.length} verified · {rejected.length} rejected
            </span>
          }
        >
          Claims
        </SectionTitle>

        {verified.length === 0 && rejected.length === 0 ? (
          <p className="text-xs text-muted-foreground">No claims were made in this run.</p>
        ) : (
          <ul className="space-y-1.5">
            {verified.map((claim) => (
              <ClaimRow key={claim.claim_id} claim={claim} ok />
            ))}
            {rejected.map((claim) => (
              <ClaimRow key={claim.claim_id} claim={claim} ok={false} />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <SectionTitle
          action={
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {toolCalls.filter((c) => c.ok).length}/{toolCalls.length} ok
            </span>
          }
        >
          Tool calls
        </SectionTitle>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          The append-only log this run&apos;s numbers are checked against.
        </p>
        <ul className="space-y-1.5">
          {toolCalls.map((call) => (
            <ToolCallRow key={call.call_id} call={call} />
          ))}
        </ul>
      </section>
    </div>
  );
}

function ClaimRow({ claim, ok }: { claim: Claim; ok: boolean }) {
  return (
    <li
      className={cn(
        "rounded-lg px-2.5 py-2 ring-1",
        ok ? "bg-card ring-foreground/10" : "bg-destructive/5 ring-destructive/25",
      )}
    >
      <div className="flex items-start gap-2">
        {ok ? (
          <CheckCircle2 className="mt-px size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <XCircle className="mt-px size-3.5 shrink-0 text-destructive" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-xs leading-snug">{claim.text}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
            <span className="font-mono">{claim.claim_id}</span>
            {claim.value !== null ? (
              <span className="font-medium text-foreground tabular-nums">
                {formatMeasure(claim.value, claim.unit)}
              </span>
            ) : null}
            <span className="font-mono">
              {claim.source_call_id ? `← ${claim.source_call_id}` : "no source call"}
            </span>
          </div>
          {!ok && claim.rejection_reason ? (
            <p className="mt-1 text-[11px] leading-relaxed text-destructive">
              {claim.rejection_reason}
            </p>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function ToolCallRow({ call }: { call: ToolCallSummary }) {
  const [open, setOpen] = useState(false);
  const args = Object.entries(call.arguments ?? {}).filter(([, v]) => v !== null && v !== undefined);

  return (
    <li className="rounded-lg bg-card ring-1 ring-foreground/10">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <Badge
          variant={call.ok ? "secondary" : "destructive"}
          className="shrink-0 font-mono text-[10px]"
        >
          {call.call_id}
        </Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{call.tool}</span>
        <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">
          {formatClock(call.called_at)}
        </span>
      </button>

      {open ? (
        <div className="space-y-1.5 border-t px-2.5 py-2">
          {args.length > 0 ? (
            <dl className="space-y-0.5">
              {args.map(([key, value]) => (
                <div key={key} className="flex gap-2 text-[10px]">
                  <dt className="shrink-0 text-muted-foreground">{key}</dt>
                  <dd className="min-w-0 flex-1 truncate text-right font-mono">
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-[10px] text-muted-foreground">no arguments</p>
          )}
          <p
            className={cn(
              "text-[11px] leading-relaxed",
              call.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive",
            )}
          >
            {call.ok ? "Succeeded — result available for verification." : (call.reason ?? EMPTY)}
          </p>
        </div>
      ) : null}
    </li>
  );
}
