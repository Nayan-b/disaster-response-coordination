"""Fact-Check agent — the guardrail.

**This is deterministic code, not a prompt.** Asking a language model to verify another language
model's numbers reintroduces exactly the failure it is supposed to catch: a confident, plausible,
unsourced figure. Verification here is arithmetic and lookup — traits code has and models don't.

Three rules, each enforced as a hard block rather than a suggestion:

1. **Every claim carrying a number must cite a tool call**, and that call must exist in this run's
   log and have succeeded. No citation, or a citation to a failed or unknown call → rejected.
2. **The number must actually appear in the cited result.** The claimed value is matched against the
   values in that tool's result payload; a figure the tool never produced is rejected even when the
   citation itself is valid. This is what stops a model citing a real call for an invented number.
3. **A source that was unavailable cannot be claimed about.** If population lookup failed, no claim
   may assert a population figure — the recommendation must say the data is missing.

A rejected claim is stripped from the recommendation and the actions that depended on it are
flagged. If a claim the recommendation depends on is rejected, the whole draft is blocked from
reaching the human rather than shown with a hole in it.
"""

from __future__ import annotations

from typing import Any

from app.models import Claim, FactCheckResult, ToolCall

# Population sums and pixel counts are rounded at several layers, so exact equality is too strict.
# 0.5% tolerance accepts rounding while still rejecting a fabricated or transposed figure.
RELATIVE_TOLERANCE = 0.005

# Claim roles the recommendation cannot stand without. A rejection here blocks the whole draft.
LOAD_BEARING_UNITS = {"people"}


def _numbers_in(payload: Any) -> list[float]:
    """Every numeric value anywhere in a tool result, flattened.

    Recursive because results nest (`per_country`, `granules`), and a claim may legitimately cite a
    nested figure such as one country's share of the population total.
    """
    found: list[float] = []
    if isinstance(payload, bool):
        return found
    if isinstance(payload, (int, float)):
        return [float(payload)]
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(_numbers_in(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found.extend(_numbers_in(value))
    return found


def _value_supported(value: float, result: dict[str, Any]) -> bool:
    candidates = _numbers_in(result)
    for candidate in candidates:
        if candidate == value:
            return True
        scale = max(abs(candidate), abs(value), 1.0)
        if abs(candidate - value) / scale <= RELATIVE_TOLERANCE:
            return True
    return False


def verify_claims(
    claims: list[Claim],
    tool_calls: list[ToolCall],
    unavailable: dict[str, str] | None = None,
) -> FactCheckResult:
    """Verify each claim against the tool-call log. Returns verified and rejected sets."""
    unavailable = unavailable or {}
    by_id = {call.call_id: call for call in tool_calls}

    verified: list[Claim] = []
    rejected: list[Claim] = []

    for claim in claims:
        reason = _reject_reason(claim, by_id, unavailable)
        if reason is None:
            verified.append(claim.model_copy(update={"verified": True, "rejection_reason": None}))
        else:
            rejected.append(
                claim.model_copy(update={"verified": False, "rejection_reason": reason})
            )

    # A rejected load-bearing figure blocks the draft: better to show the responder nothing than a
    # recommendation whose central number failed verification.
    blocking = [c for c in rejected if (c.unit or "") in LOAD_BEARING_UNITS]
    blocking_reason = None
    if blocking:
        blocking_reason = (
            f"{len(blocking)} load-bearing claim(s) failed verification and were removed: "
            + "; ".join(f"{c.claim_id} ({c.rejection_reason})" for c in blocking)
        )

    return FactCheckResult(
        verified_claims=verified,
        rejected_claims=rejected,
        passed=not blocking,
        blocking_reason=blocking_reason,
    )


def _reject_reason(
    claim: Claim,
    by_id: dict[str, ToolCall],
    unavailable: dict[str, str],
) -> str | None:
    """None means the claim passes; a string is the reason it was rejected."""
    # Rule 3 first: if the underlying source failed, nothing may be asserted about it.
    if claim.unit == "people" and "population" in unavailable:
        return f"population data is unavailable ({unavailable['population']}), so no figure may be claimed"

    # A qualitative claim with no number needs no citation — there is nothing to fabricate.
    if claim.value is None:
        return None

    # Rule 1: numbers must cite a successful tool call from this run.
    if not claim.source_call_id:
        return "numeric claim cites no tool call"
    call = by_id.get(claim.source_call_id)
    if call is None:
        return f"cites unknown tool call {claim.source_call_id!r}"
    if not call.ok:
        return (
            f"cites failed tool call {claim.source_call_id} "
            f"({call.result.get('reason', 'unavailable')})"
        )

    # Rule 2: the number must be present in that call's result.
    if not _value_supported(claim.value, call.result):
        return (
            f"value {claim.value:,.0f} does not appear in {claim.source_call_id} "
            f"({call.tool}) — unsupported by its result"
        )

    return None


def strip_unverified(
    recommendation_actions: list[Any], verified_ids: set[str]
) -> list[Any]:
    """Drop supporting-claim references that did not survive verification.

    The action itself is kept — a responder still benefits from seeing the proposed step — but it can
    no longer point at a claim the Fact-Check agent rejected.
    """
    cleaned = []
    for action in recommendation_actions:
        kept = [cid for cid in action.supporting_claim_ids if cid in verified_ids]
        cleaned.append(action.model_copy(update={"supporting_claim_ids": kept}))
    return cleaned
