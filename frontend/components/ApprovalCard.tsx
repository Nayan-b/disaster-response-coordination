"use client";

/**
 * The human-in-the-loop approval card.
 *
 * UI copy is deliberate: this is a *recommendation for review*, the reviewer can approve/edit/reject,
 * and approving records a decision rather than dispatching anything. Verified claims are shown with
 * the tool call that backs them, and anything the Fact-Check agent rejected is shown too — a
 * reviewer should see what was thrown out, not just what survived.
 */

import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import type { ApprovalRequest } from "@/lib/types";

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
  onDecide: (decision: Decision, notes: string, editedActions?: unknown[]) => void;
}) {
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [draftActions, setDraftActions] = useState(() =>
    (request.recommendation?.actions ?? []).map((a) => a.action).join("\n"),
  );

  const recommendation = request.recommendation;
  if (!recommendation) {
    return (
      <Alert variant="destructive">
        <AlertTitle>No recommendation to review</AlertTitle>
        <AlertDescription>
          The run paused without a draft. Check the trace for the blocking reason.
        </AlertDescription>
      </Alert>
    );
  }

  const claimById = new Map(request.verified_claims.map((c) => [c.claim_id, c]));
  const isRuleBased = recommendation.drafted_by === "rule-based";

  const submitEdit = () => {
    const lines = draftActions
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const edited = lines.map((action, index) => {
      const original = recommendation.actions[index];
      return {
        priority: index + 1,
        action,
        rationale: original?.rationale ?? "Edited by the reviewing responder.",
        supporting_claim_ids: original?.supporting_claim_ids ?? [],
      };
    });
    onDecide("edited", notes, edited);
  };

  return (
    <Card className="border-amber-500/50 shadow-sm">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge className="bg-amber-500 text-amber-950 hover:bg-amber-500">
            Awaiting your approval
          </Badge>
          <Badge variant="outline" className="font-mono text-[10px]">
            drafted by {recommendation.drafted_by}
          </Badge>
          {isRuleBased ? (
            <Badge variant="outline" className="text-[10px]">
              no LLM configured — deterministic draft
            </Badge>
          ) : null}
        </div>
        <CardTitle className="text-base leading-snug">{recommendation.headline}</CardTitle>
        <p className="text-xs leading-relaxed text-muted-foreground">{recommendation.situation}</p>
      </CardHeader>

      <CardContent className="space-y-4">
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Recommended actions
          </h3>
          {editing ? (
            <div className="mt-2 space-y-2">
              <Textarea
                value={draftActions}
                onChange={(event) => setDraftActions(event.target.value)}
                rows={Math.max(4, recommendation.actions.length + 1)}
                className="text-xs"
                aria-label="Edit recommended actions, one per line"
              />
              <p className="text-[11px] text-muted-foreground">
                One action per line, highest priority first.
              </p>
            </div>
          ) : (
            <ol className="mt-2 space-y-2.5">
              {recommendation.actions.map((action) => (
                <li key={`${action.priority}-${action.action}`} className="text-xs">
                  <div className="flex gap-2">
                    <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold tabular-nums">
                      {action.priority}
                    </span>
                    <div className="min-w-0 space-y-1">
                      <p className="font-medium leading-snug">{action.action}</p>
                      <p className="leading-relaxed text-muted-foreground">{action.rationale}</p>
                      {action.supporting_claim_ids.length > 0 ? (
                        <ul className="space-y-0.5">
                          {action.supporting_claim_ids.map((id) => {
                            const claim = claimById.get(id);
                            return (
                              <li key={id} className="text-[11px] text-muted-foreground">
                                <span className="font-mono text-emerald-600 dark:text-emerald-400">
                                  ✓ {claim?.source_call_id ?? id}
                                </span>{" "}
                                {claim?.text ?? id}
                              </li>
                            );
                          })}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        {recommendation.caveats.length > 0 ? (
          <>
            <Separator />
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Caveats
              </h3>
              <ul className="mt-2 space-y-1">
                {recommendation.caveats.map((caveat) => (
                  <li key={caveat} className="text-[11px] leading-relaxed text-muted-foreground">
                    • {caveat}
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : null}

        {request.rejected_claims.length > 0 ? (
          <Alert variant="destructive">
            <AlertTitle className="text-xs">
              {request.rejected_claims.length} claim(s) removed by the fact-check
            </AlertTitle>
            <AlertDescription>
              <ul className="mt-1 space-y-1">
                {request.rejected_claims.map((claim) => (
                  <li key={claim.claim_id} className="text-[11px]">
                    <span className="font-mono">{claim.claim_id}</span>: {claim.rejection_reason}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        <Separator />

        <div className="space-y-2">
          <label htmlFor="reviewer-notes" className="text-xs font-medium">
            Notes {editing ? "" : "(optional)"}
          </label>
          <Textarea
            id="reviewer-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Context for the action log — why you approved, changed, or rejected this."
            rows={2}
            className="text-xs"
          />
        </div>

        <p className="rounded-md bg-muted/60 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          {request.notice}
        </p>

        <div className="flex flex-wrap gap-2">
          {editing ? (
            <>
              <Button size="sm" onClick={submitEdit} disabled={submitting}>
                Save &amp; approve edited
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(false)}
                disabled={submitting}
              >
                Cancel edit
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                className="bg-emerald-600 hover:bg-emerald-700"
                onClick={() => onDecide("approved", notes)}
                disabled={submitting}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(true)}
                disabled={submitting}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => onDecide("rejected", notes)}
                disabled={submitting}
              >
                Reject
              </Button>
            </>
          )}
          <span className="ml-auto self-center text-[11px] text-muted-foreground">
            reviewing as <span className="font-medium">{reviewer}</span>
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
