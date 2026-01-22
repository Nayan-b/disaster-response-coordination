"use client";

/**
 * The human-in-the-loop gate.
 *
 * The copy is deliberate: this is a *recommendation for review*, and approving records a decision
 * rather than dispatching anything. Two things a reviewer must see before deciding are kept in
 * view — what the fact-check threw out, and which sources were unavailable — because a brief is
 * judged as much by what is missing from it as by what it says.
 *
 * Editing is per-action rather than a free-text blob. The old textarea silently re-paired edited
 * text with the original action's rationale and claim ids by list position, so deleting a line
 * re-attributed every citation below it. Here an edited action keeps its own rationale and
 * citations, and a removed one takes its citations with it.
 */

import { AlertTriangle, Check, Loader2, Pencil, Undo2, X } from "lucide-react";
import { useState } from "react";

import { ActionList, EngineBadge } from "@/components/RecommendationView";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SectionTitle } from "@/components/ui/data-display";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { humanize } from "@/lib/format";
import type { AllocationAction, ApprovalRequest, Claim } from "@/lib/types";

type Decision = "approved" | "edited" | "rejected";

export function ApprovalCard({
  request,
  reviewer,
  submitting,
  onDecide,
}: {
  request: ApprovalRequest;
  reviewer: string;
  submitting: boolean;
  onDecide: (decision: Decision, notes: string, editedActions?: AllocationAction[]) => void;
}) {
  const recommendation = request.recommendation;
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<AllocationAction[]>(() => recommendation?.actions ?? []);

  if (!recommendation) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="size-4" />
        <AlertTitle className="text-xs">No recommendation to review</AlertTitle>
        <AlertDescription className="text-[11px]">
          The run paused without a draft. Check the trace for the blocking reason.
        </AlertDescription>
      </Alert>
    );
  }

  const claimById = new Map<string, Claim>(
    request.verified_claims.map((claim) => [claim.claim_id, claim]),
  );
  // The drafter already folds unavailable sources into its caveats. Listing them again below, word
  // for word, pads the card with a paragraph the reviewer has just read — so only gaps the caveats
  // don't already carry get their own block.
  const unavailableEntries = Object.entries(request.unavailable ?? {}).filter(
    ([, reason]) => !recommendation.caveats.some((caveat) => caveat.includes(reason)),
  );
  const changed =
    draft.length !== recommendation.actions.length ||
    draft.some((action, index) => action.action !== recommendation.actions[index]?.action);

  const cancelEdit = () => {
    setDraft(recommendation.actions);
    setEditing(false);
  };

  const submitEdit = () => {
    const cleaned = draft
      .map((action) => ({ ...action, action: action.action.trim() }))
      .filter((action) => action.action.length > 0)
      .map((action, index) => ({ ...action, priority: index + 1 }));
    onDecide("edited", notes, cleaned);
  };

  return (
    <section
      aria-label="Approval required"
      // No `overflow-hidden` here: it would make this card the containing block for the sticky
      // action bar below, pinning the bar to the card's own bottom instead of the panel's.
      className="rounded-xl bg-card ring-2 ring-amber-500/60"
    >
      {/* Banner — the run has stopped, and this says so before anything else. */}
      <div className="flex flex-wrap items-center gap-2 rounded-t-xl bg-amber-500/12 px-3.5 py-2.5">
        <span className="flex size-2 shrink-0 animate-pulse rounded-full bg-amber-500" />
        <span className="text-xs font-semibold text-amber-700 dark:text-amber-300">
          Paused — awaiting your decision
        </span>
        <span className="ml-auto flex items-center gap-2">
          <EngineBadge
            draftedBy={recommendation.drafted_by}
            fallbackReason={request.unavailable?.allocation_llm ?? null}
          />
        </span>
      </div>

      <div className="space-y-4 px-3.5 py-3.5">
        <header className="space-y-1.5">
          <h2 className="text-sm leading-snug font-semibold text-balance">
            {recommendation.headline}
          </h2>
          <p className="text-xs leading-relaxed text-muted-foreground">{recommendation.situation}</p>
        </header>

        <section className="space-y-2">
          <SectionTitle
            action={
              !editing ? (
                <Button size="xs" variant="ghost" onClick={() => setEditing(true)} disabled={submitting}>
                  <Pencil /> Edit
                </Button>
              ) : (
                <Button size="xs" variant="ghost" onClick={cancelEdit} disabled={submitting}>
                  <Undo2 /> Revert
                </Button>
              )
            }
          >
            Recommended actions
          </SectionTitle>

          {editing ? (
            <ActionEditor actions={draft} onChange={setDraft} disabled={submitting} />
          ) : (
            <ActionList actions={recommendation.actions} claimById={claimById} />
          )}
        </section>

        {recommendation.caveats.length > 0 ? (
          <section className="space-y-1.5">
            <SectionTitle>Caveats</SectionTitle>
            <ul className="space-y-1 rounded-lg bg-muted/50 px-3 py-2">
              {recommendation.caveats.map((caveat) => (
                <li key={caveat} className="text-[11px] leading-relaxed text-muted-foreground">
                  • {caveat}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {request.rejected_claims.length > 0 ? (
          <Alert variant="destructive">
            <X className="size-4" />
            <AlertTitle className="text-xs">
              {request.rejected_claims.length} claim
              {request.rejected_claims.length === 1 ? "" : "s"} removed by the fact-check
            </AlertTitle>
            <AlertDescription>
              <ul className="mt-1 space-y-1">
                {request.rejected_claims.map((claim) => (
                  <li key={claim.claim_id} className="text-[11px] leading-relaxed">
                    <span className="font-mono">{claim.claim_id}</span> — {claim.rejection_reason}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        {unavailableEntries.length > 0 ? (
          <div className="rounded-lg bg-muted/50 px-3 py-2">
            <p className="text-[11px] font-medium">Decide with these gaps in mind</p>
            <ul className="mt-1 space-y-1">
              {unavailableEntries.map(([role, reason]) => (
                <li key={role} className="text-[11px] leading-relaxed text-muted-foreground">
                  <span className="font-medium">{humanize(role)}:</span> {reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <Separator />

        <div className="space-y-1.5">
          <label htmlFor="reviewer-notes" className="text-xs font-medium">
            Notes for the action log{" "}
            <span className="font-normal text-muted-foreground">
              {editing ? "(what you changed and why)" : "(optional)"}
            </span>
          </label>
          <Textarea
            id="reviewer-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Why you approved, changed, or rejected this."
            rows={2}
            className="text-xs"
          />
        </div>

        <p className="rounded-lg bg-muted/60 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          {request.notice}
        </p>

        {/* Sticky: the brief runs long, and a reviewer should never have to scroll to the bottom to
            find out how to act on it. */}
        <div className="sticky bottom-0 -mx-3.5 -mb-3.5 flex flex-wrap items-center gap-2 rounded-b-xl border-t bg-card/95 px-3.5 py-2.5 backdrop-blur">
          {editing ? (
            <>
              <Button size="sm" onClick={submitEdit} disabled={submitting || draft.length === 0}>
                {submitting ? <Loader2 className="animate-spin" /> : <Check />}
                Approve with edits
              </Button>
              <Button size="sm" variant="outline" onClick={cancelEdit} disabled={submitting}>
                Cancel
              </Button>
              {changed ? (
                <Badge variant="outline" className="text-[10px]">
                  {draft.length} action{draft.length === 1 ? "" : "s"} · modified
                </Badge>
              ) : null}
            </>
          ) : (
            <>
              <Button
                size="sm"
                className="bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={() => onDecide("approved", notes)}
                disabled={submitting}
              >
                {submitting ? <Loader2 className="animate-spin" /> : <Check />}
                Approve as drafted
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => onDecide("rejected", notes)}
                disabled={submitting}
              >
                <X /> Reject
              </Button>
            </>
          )}
          <span className="ml-auto text-[11px] text-muted-foreground">
            reviewing as <span className="font-medium text-foreground">{reviewer}</span>
          </span>
        </div>
      </div>
    </section>
  );
}

/**
 * Per-action editing. Each row keeps its own rationale and supporting claim ids, so a reviewer can
 * reword or drop an action without silently re-attributing anyone else's citations.
 */
function ActionEditor({
  actions,
  onChange,
  disabled,
}: {
  actions: AllocationAction[];
  onChange: (next: AllocationAction[]) => void;
  disabled: boolean;
}) {
  const update = (index: number, text: string) =>
    onChange(actions.map((action, i) => (i === index ? { ...action, action: text } : action)));

  const remove = (index: number) =>
    onChange(
      actions
        .filter((_, i) => i !== index)
        .map((action, i) => ({ ...action, priority: i + 1 })),
    );

  const add = () =>
    onChange([
      ...actions,
      {
        priority: actions.length + 1,
        action: "",
        rationale: "Added by the reviewing responder.",
        supporting_claim_ids: [],
      },
    ]);

  return (
    <div className="space-y-2">
      {actions.map((action, index) => (
        <div
          key={index}
          className="flex items-start gap-2 rounded-lg bg-card p-2 ring-1 ring-foreground/10"
        >
          <span className="mt-1.5 flex size-5 shrink-0 items-center justify-center rounded-md bg-muted text-[11px] font-semibold tabular-nums">
            {index + 1}
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <Textarea
              value={action.action}
              onChange={(event) => update(index, event.target.value)}
              rows={2}
              disabled={disabled}
              className="text-xs"
              aria-label={`Action ${index + 1}`}
              placeholder="What should the team do?"
            />
            {action.supporting_claim_ids.length > 0 ? (
              <p className="text-[10px] text-muted-foreground">
                keeps citations:{" "}
                <span className="font-mono">{action.supporting_claim_ids.join(", ")}</span>
              </p>
            ) : null}
          </div>
          <Button
            size="icon-xs"
            variant="ghost"
            onClick={() => remove(index)}
            disabled={disabled}
            aria-label={`Remove action ${index + 1}`}
          >
            <X />
          </Button>
        </div>
      ))}
      <Button size="xs" variant="outline" onClick={add} disabled={disabled}>
        Add an action
      </Button>
    </div>
  );
}
