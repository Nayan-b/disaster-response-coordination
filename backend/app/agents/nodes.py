"""Graph nodes.

The node split follows the brief's architecture, with one deliberate design choice: **the three
data-gathering agents are deterministic, and the LLM is reserved for judgment.**

Alert monitoring, imagery retrieval, and population overlay have a fixed correct call sequence
(extent → population → imagery → tiles). Handing that to a model adds latency, cost, and a
fabrication surface for no gain — there is no judgment in "call the next obvious tool". The LLM earns
its place in the two nodes that genuinely require judgment: triaging which of several active events
is most urgent, and drafting the allocation recommendation. Fact-checking is deterministic on
principle (see `factcheck.py`).

Every node appends to `trace`, which is what the UI streams as the reasoning trace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents import llm
from app.agents.factcheck import strip_unverified, verify_claims
from app.agents.state import FloodState, TraceEvent
from app.models import (
    AllocationAction,
    Claim,
    Recommendation,
    ToolCall,
)
from app.tools import ToolRunner

ALERT_RANK = {"Red": 3, "Orange": 2, "Green": 1}


def _trace(node: str, message: str, level: str = "info") -> TraceEvent:
    return TraceEvent(
        node=node, message=message, at=datetime.now(UTC).isoformat(), level=level
    )


def _runner_from(state: FloodState) -> ToolRunner:
    """A runner seeded with prior calls so call ids stay unique and the log stays append-only."""
    return ToolRunner(calls=list(state.get("tool_calls") or []))


def _new_calls(runner: ToolRunner, state: FloodState) -> list[ToolCall]:
    """Only the calls this node added — the state reducer concatenates them."""
    return runner.calls[len(state.get("tool_calls") or []) :]


# ---------------------------------------------------------------------------
# 1. Alert Monitor
# ---------------------------------------------------------------------------


class _Triage(BaseModel):
    """Structured triage decision when several events are active."""

    event_id: int = Field(description="The event_id to prioritize from the supplied candidates.")
    reason: str = Field(description="One sentence on why this event is the most urgent.")


def alert_monitor(state: FloodState) -> dict[str, Any]:
    """Find flood events and pick which one to work on.

    Deterministic listing; the LLM only breaks ties between several active events, which is a real
    judgment call. The deterministic ordering (alert level, then recency) is the fallback and is also
    what validates the model's choice — a triage pick outside the candidate set is discarded.
    """
    runner = _runner_from(state)
    days = state.get("lookback_days") or 30
    levels = state.get("alert_levels") or ["Orange", "Red"]

    result = runner.invoke("list_flood_events", {"days": days, "alert_levels": levels})
    trace = [_trace("alert_monitor", f"Queried GDACS for flood events, last {days} days, {levels}")]

    if not result.get("ok"):
        return {
            "tool_calls": _new_calls(runner, state),
            "trace": trace + [_trace("alert_monitor", result["reason"], "warning")],
            "unavailable": {**(state.get("unavailable") or {}), "events": result["reason"]},
            "status": "no_events",
        }

    candidates: list[dict[str, Any]] = result["events"]
    trace.append(_trace("alert_monitor", f"Found {len(candidates)} event(s) above threshold"))

    requested = state.get("event_id")
    chosen: dict[str, Any] | None = None
    if requested:
        chosen = next((e for e in candidates if e["event_id"] == requested), None)
        if chosen is None:
            # A specific event was asked for and is not in the window — say so rather than
            # silently substituting a different event.
            reason = f"event {requested} is not among the {len(candidates)} events in the last {days} days"
            return {
                "tool_calls": _new_calls(runner, state),
                "trace": trace + [_trace("alert_monitor", reason, "error")],
                "unavailable": {**(state.get("unavailable") or {}), "events": reason},
                "status": "event_not_found",
            }

    if chosen is None:
        chosen = _rank_events(candidates)[0]
        if len(candidates) > 1:
            picked = _llm_triage(candidates)
            if picked is not None:
                match = next((e for e in candidates if e["event_id"] == picked.event_id), None)
                if match is not None:
                    chosen = match
                    trace.append(
                        _trace("alert_monitor", f"Triage ({llm.active_engine()}): {picked.reason}")
                    )
            else:
                trace.append(
                    _trace(
                        "alert_monitor",
                        f"Triage by rule: highest alert level, then most recent "
                        f"→ {chosen['alert_level']} in {chosen['country']}",
                    )
                )

    trace.append(
        _trace(
            "alert_monitor",
            f"Selected event {chosen['event_id']} ({chosen['country']}, {chosen['alert_level']})",
        )
    )
    return {
        "candidate_events": candidates,
        "event": chosen,
        "event_id": chosen["event_id"],
        "episode_id": chosen.get("episode_id"),
        "tool_calls": _new_calls(runner, state),
        "trace": trace,
        "status": "event_selected",
    }


def _rank_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda e: (ALERT_RANK.get(e.get("alert_level") or "", 0), e.get("to_date") or ""),
        reverse=True,
    )


def _llm_triage(candidates: list[dict[str, Any]]) -> _Triage | None:
    if not llm.llm_available():
        return None
    summary = json.dumps(
        [
            {k: e.get(k) for k in ("event_id", "country", "alert_level", "from_date", "to_date")}
            for e in candidates
        ],
        indent=1,
    )
    try:
        return llm.structured_completion(  # type: ignore[return-value]
            _Triage,
            system=(
                "You triage active flood events for a disaster-response team. Choose the single "
                "most urgent event from the candidates supplied. Weigh alert level first, then how "
                "recently the event window ends. Choose only from the given event_ids — do not "
                "invent one. Reply with the schema only."
            ),
            user=f"Active flood events:\n{summary}\n\nWhich event should the team work on first?",
            effort="low",  # a tie-break, not a hard reasoning task
        )
    except llm.LLMUnavailable:
        return None


# ---------------------------------------------------------------------------
# 2. Imagery Retrieval
# ---------------------------------------------------------------------------


def imagery_retrieval(state: FloodState) -> dict[str, Any]:
    """Fetch the flood extent, granule references, and a map tile layer."""
    runner = _runner_from(state)
    event_id = state["event_id"]
    episode_id = state.get("episode_id")
    trace: list[TraceEvent] = []
    unavailable = dict(state.get("unavailable") or {})
    out: dict[str, Any] = {}

    extent = runner.invoke("get_flood_extent", {"event_id": event_id, "episode_id": episode_id})
    if extent.get("ok"):
        out["extent"] = extent
        trace.append(
            _trace(
                "imagery_retrieval",
                f"Affected-area {extent['geometry_type']} → bbox {extent['bbox']} "
                f"({extent['area_sq_deg']} deg²)",
            )
        )
    else:
        unavailable["extent"] = extent["reason"]
        trace.append(_trace("imagery_retrieval", f"No flood extent: {extent['reason']}", "warning"))
        # Without an extent there is nothing to image or measure — stop here rather than
        # reporting three failures that share one root cause.
        unavailable.setdefault("imagery", "no flood extent available to search imagery for")
        unavailable.setdefault("population", "no flood extent available to sum population over")
        return {
            "tool_calls": _new_calls(runner, state),
            "trace": trace,
            "unavailable": unavailable,
            "status": "no_extent",
        }

    imagery = runner.invoke(
        "search_flood_imagery", {"event_id": event_id, "episode_id": episode_id, "days": 7}
    )
    if imagery.get("ok"):
        out["imagery"] = imagery
        trace.append(
            _trace(
                "imagery_retrieval",
                f"CMR-STAC: {imagery['granule_count']} granule(s) of {imagery['collection']} "
                f"(matched {imagery['matched']})",
            )
        )
    else:
        unavailable["imagery"] = imagery["reason"]
        trace.append(
            _trace("imagery_retrieval", "No NRT granules; falling back to GIBS tiles", "warning")
        )

    # Always available — no network call — which is why it backs up the granule search.
    tiles = runner.invoke("get_tile_layer", {"event_id": event_id, "episode_id": episode_id})
    if tiles.get("ok"):
        out["tiles"] = tiles
        trace.append(_trace("imagery_retrieval", f"Map layer {tiles['layer']} @ {tiles['date']}"))

    return {
        **out,
        "tool_calls": _new_calls(runner, state),
        "trace": trace,
        "unavailable": unavailable,
        "status": "imagery_done",
    }


# ---------------------------------------------------------------------------
# 3. Population Overlay
# ---------------------------------------------------------------------------


def population_overlay(state: FloodState) -> dict[str, Any]:
    """Sum WorldPop population inside the flood polygon."""
    runner = _runner_from(state)
    unavailable = dict(state.get("unavailable") or {})

    if not state.get("extent"):
        return {"trace": [], "unavailable": unavailable, "status": "population_skipped"}

    result = runner.invoke(
        "estimate_population",
        {"event_id": state["event_id"], "episode_id": state.get("episode_id")},
    )
    if not result.get("ok"):
        unavailable["population"] = result["reason"]
        return {
            "tool_calls": _new_calls(runner, state),
            "trace": [_trace("population_overlay", f"No population data: {result['reason']}", "warning")],
            "unavailable": unavailable,
            "status": "population_unavailable",
        }

    trace = [
        _trace(
            "population_overlay",
            f"{result['population']:,} people inside the flood polygon "
            f"({result['pixels_in_polygon']:,} px, WorldPop {result['raster_year']} "
            f"{result['resolution']} UN-adjusted)",
        )
    ]
    if result.get("is_partial"):
        trace.append(
            _trace(
                "population_overlay",
                f"PARTIAL: no raster for {result['countries_missing']} — total understates reality",
                "warning",
            )
        )
    return {
        "population": result,
        "tool_calls": _new_calls(runner, state),
        "trace": trace,
        "unavailable": unavailable,
        "status": "population_done",
    }


# ---------------------------------------------------------------------------
# 4. Allocation draft (LLM or rule-based)
# ---------------------------------------------------------------------------


class _DraftAction(BaseModel):
    priority: int = Field(description="1 is highest priority.")
    action: str = Field(description="The concrete action being recommended.")
    rationale: str = Field(description="Why, referencing the evidence.")
    supporting_claim_ids: list[str] = Field(
        description="claim_ids from the supplied evidence that support this action."
    )


class _Draft(BaseModel):
    headline: str = Field(description="One line summarizing the situation.")
    situation: str = Field(description="2-4 sentences of factual situation summary.")
    actions: list[_DraftAction] = Field(description="Proposed actions, highest priority first.")
    caveats: list[str] = Field(description="Data gaps and limitations a responder must know.")


ALLOCATION_SYSTEM = """\
You draft resource-allocation recommendations for a human flood-response coordinator.

Your output is a RECOMMENDATION FOR HUMAN REVIEW. It is never an instruction, never a completed
action, and nothing you write is executed by any system. A human responder approves, edits, or
rejects it.

Hard rules on evidence:
- You are given a list of verified facts, each with a claim_id. Every number you state must come
  from one of those facts, and you must cite its claim_id in supporting_claim_ids.
- Do not state, estimate, interpolate, or infer any number that is not in the supplied facts. There
  is no partial credit for a plausible figure — an unsourced number is removed by an automatic
  fact-check step and may block the whole recommendation from reaching the responder.
- You are also given a list of unavailable data. Say plainly in caveats what is missing. Never fill
  a gap with an estimate.
- If the population figure is marked partial, say so wherever you use it: it understates reality.

Write for a responder who is scanning under time pressure: lead with what matters, keep the
rationale concrete, and prefer plain sentences over hedging."""


def allocation_draft(state: FloodState) -> dict[str, Any]:
    """Draft the recommendation and the claims that support it.

    Claims are built deterministically from the tool results *before* drafting, so each carries a
    real `source_call_id`. The model then references claim ids rather than inventing figures — it
    cannot cite something that does not exist, and the Fact-Check node verifies whatever it does cite.
    """
    claims = _build_claims(state)
    unavailable = dict(state.get("unavailable") or {})
    engine = llm.active_engine()

    draft: _Draft | None = None
    if llm.llm_available():
        try:
            draft = _llm_draft(state, claims, unavailable)  # type: ignore[assignment]
        except llm.LLMUnavailable as exc:
            engine = llm.ENGINE_RULE_BASED
            unavailable["allocation_llm"] = str(exc)

    if draft is None:
        engine = llm.ENGINE_RULE_BASED
        draft = _rule_based_draft(state, claims, unavailable)

    recommendation = Recommendation(
        headline=draft.headline,
        situation=draft.situation,
        actions=[
            AllocationAction(
                priority=a.priority,
                action=a.action,
                rationale=a.rationale,
                supporting_claim_ids=list(a.supporting_claim_ids),
            )
            for a in sorted(draft.actions, key=lambda a: a.priority)
        ],
        caveats=list(draft.caveats),
        drafted_by=engine,
        drafted_at=datetime.now(UTC),
    )
    return {
        "claims": claims,
        "recommendation": recommendation,
        "engine": engine,
        "unavailable": unavailable,
        "trace": [
            _trace(
                "allocation_draft",
                f"Drafted {len(recommendation.actions)} recommended action(s) via {engine} "
                f"from {len(claims)} candidate claim(s)",
            )
        ],
        "status": "drafted",
    }


def _build_claims(state: FloodState) -> list[Claim]:
    """Turn tool results into citable claims, each tied to the call that produced it."""
    calls = state.get("tool_calls") or []

    def call_id_for(tool: str) -> str | None:
        for call in reversed(calls):
            if call.tool == tool and call.ok:
                return call.call_id
        return None

    claims: list[Claim] = []
    event = state.get("event") or {}
    population = state.get("population")
    extent = state.get("extent")
    imagery = state.get("imagery")

    if event:
        claims.append(
            Claim(
                claim_id="c_alert",
                text=(
                    f"GDACS rates this event {event.get('alert_level')} overall "
                    f"(latest episode {event.get('episode_alert_level')}) in {event.get('country')}"
                ),
                source_call_id=call_id_for("list_flood_events"),
            )
        )
    if population:
        claims.append(
            Claim(
                claim_id="c_population",
                text=(
                    f"Approximately {population['population']:,} people are inside the flood "
                    f"extent (WorldPop {population['raster_year']}, {population['resolution']}, "
                    f"UN-adjusted)"
                    + (" — PARTIAL, some countries missing" if population.get("is_partial") else "")
                ),
                value=float(population["population"]),
                unit="people",
                source_call_id=call_id_for("estimate_population"),
            )
        )
        for iso3, value in (population.get("per_country") or {}).items():
            if len(population.get("per_country") or {}) > 1:
                claims.append(
                    Claim(
                        claim_id=f"c_pop_{iso3.lower()}",
                        text=f"{value:,} of those people are in {iso3}",
                        value=float(value),
                        unit="people",
                        source_call_id=call_id_for("estimate_population"),
                    )
                )
    if extent:
        claims.append(
            Claim(
                claim_id="c_extent",
                text=(
                    f"The affected area spans bbox {extent['bbox']} "
                    f"({extent['area_sq_deg']} deg²), derived from the GDACS affected-area polygon"
                ),
                value=float(extent["area_sq_deg"]),
                unit="deg2",
                source_call_id=call_id_for("get_flood_extent"),
            )
        )
    if imagery:
        claims.append(
            Claim(
                claim_id="c_imagery",
                text=(
                    f"{imagery['granule_count']} NASA {imagery['collection']} flood granule(s) "
                    f"cover the area"
                ),
                value=float(imagery["granule_count"]),
                unit="granules",
                source_call_id=call_id_for("search_flood_imagery"),
            )
        )
    return claims


def _evidence_block(claims: list[Claim]) -> str:
    return json.dumps(
        [
            {"claim_id": c.claim_id, "fact": c.text, "value": c.value, "unit": c.unit}
            for c in claims
        ],
        indent=1,
    )


def _llm_draft(state: FloodState, claims: list[Claim], unavailable: dict[str, str]) -> _Draft:
    event = state.get("event") or {}
    user = (
        f"Flood event under review:\n{json.dumps(event, indent=1)}\n\n"
        f"VERIFIED FACTS you may cite (use these claim_ids):\n{_evidence_block(claims)}\n\n"
        f"UNAVAILABLE data (state these as caveats; do not estimate around them):\n"
        f"{json.dumps(unavailable, indent=1) if unavailable else 'none'}\n\n"
        "Draft a resource-allocation recommendation for the human coordinator."
    )
    return llm.structured_completion(  # type: ignore[return-value]
        _Draft, system=ALLOCATION_SYSTEM, user=user, effort="high"
    )


def _rule_based_draft(
    state: FloodState, claims: list[Claim], unavailable: dict[str, str]
) -> _Draft:
    """Deterministic recommendation used when no LLM is configured.

    Produces the same structure from the same evidence, so the fact-check and approval path is
    exercised identically. Every figure comes from a claim, so it passes verification by construction.
    """
    event = state.get("event") or {}
    population = state.get("population")
    extent = state.get("extent")
    country = event.get("country") or "the affected area"
    alert = event.get("alert_level") or "unrated"

    people = int(population["population"]) if population else None
    partial = bool(population and population.get("is_partial"))

    if people is None:
        headline = f"{alert} flood alert in {country} — population exposure unknown"
    else:
        headline = f"{alert} flood alert in {country} — ~{people:,} people in the flood extent"
        if partial:
            headline += " (partial coverage)"

    situation_parts = [
        f"GDACS rates this event {alert} overall"
        + (f", with its latest episode at {event.get('episode_alert_level')}" if event.get("episode_alert_level") else "")
        + f", covering {country}."
    ]
    if event.get("from_date") and event.get("to_date"):
        situation_parts.append(f"The event window runs {event['from_date']} to {event['to_date']}.")
    if extent:
        situation_parts.append(
            f"The affected-area polygon spans {extent['area_sq_deg']} deg² "
            f"(bbox {extent['bbox']})."
        )
    if people is not None:
        situation_parts.append(
            f"Roughly {people:,} people fall inside that polygon per WorldPop "
            f"{population['raster_year']} ({population['resolution']}, UN-adjusted)."
        )
    situation = " ".join(situation_parts)

    actions: list[_DraftAction] = []
    priority = 1

    if people is not None:
        # Thresholds are declared here rather than hidden in prose so a reviewer can audit them.
        scale = "large-scale" if people > 1_000_000 else "localized"
        actions.append(
            _DraftAction(
                priority=priority,
                action=(
                    f"Stage {scale} relief capacity for the population inside the flood extent "
                    f"(~{people:,} people)"
                ),
                rationale=(
                    f"Population inside the affected-area polygon is ~{people:,} per WorldPop "
                    f"{population['raster_year']}; the {alert} alert level indicates active flooding."
                ),
                supporting_claim_ids=["c_population", "c_alert"],
            )
        )
        priority += 1

        multi = population.get("per_country") or {}
        if len(multi) > 1:
            biggest = max(multi, key=lambda k: multi[k])
            actions.append(
                _DraftAction(
                    priority=priority,
                    action=f"Coordinate first with {biggest} authorities",
                    rationale=(
                        f"{biggest} holds the largest share of exposed population "
                        f"({multi[biggest]:,} people) among the affected countries."
                    ),
                    supporting_claim_ids=[f"c_pop_{biggest.lower()}"],
                )
            )
            priority += 1
    else:
        actions.append(
            _DraftAction(
                priority=priority,
                action="Obtain population exposure before committing relief capacity",
                rationale=(
                    "No population figure could be computed for this event, so exposure is unknown. "
                    "Committing capacity without it would be guesswork."
                ),
                supporting_claim_ids=["c_alert"] if state.get("event") else [],
            )
        )
        priority += 1

    if extent:
        actions.append(
            _DraftAction(
                priority=priority,
                action=f"Task assessment teams to the affected-area bbox {extent['bbox']}",
                rationale=(
                    f"The affected-area polygon covers {extent['area_sq_deg']} deg²; the bbox bounds "
                    "where ground assessment should concentrate."
                ),
                supporting_claim_ids=["c_extent"],
            )
        )
        priority += 1

    if state.get("tiles"):
        actions.append(
            _DraftAction(
                priority=priority,
                action="Brief the operations team using the NASA GIBS flood imagery layer",
                rationale=(
                    "Near-real-time flood imagery for the event date is available as a map layer "
                    "for situational briefing."
                ),
                supporting_claim_ids=[],
            )
        )

    caveats: list[str] = []
    if partial:
        caveats.append(
            f"Population is PARTIAL: no raster for {population['countries_missing']}, so the "
            "figure understates the true affected population."
        )
    if population:
        caveats.append(
            f"Population raster is {population['raster_year']}; the event is "
            f"{(event.get('from_date') or '')[:4] or 'later'}, so exposure may have shifted."
        )
    for role, reason in unavailable.items():
        caveats.append(f"{role} unavailable: {reason}")
    caveats.append(
        "This is a recommendation for human review, not an instruction — no action has been taken."
    )

    return _Draft(headline=headline, situation=situation, actions=actions, caveats=caveats)


# ---------------------------------------------------------------------------
# 5. Fact-Check
# ---------------------------------------------------------------------------


def fact_check(state: FloodState) -> dict[str, Any]:
    """Verify every claim against the tool-call log. Deterministic — see factcheck.py."""
    claims = state.get("claims") or []
    result = verify_claims(
        claims, state.get("tool_calls") or [], state.get("unavailable") or {}
    )

    trace = [
        _trace(
            "fact_check",
            f"Verified {len(result.verified_claims)}/{len(claims)} claim(s) against "
            f"{len(state.get('tool_calls') or [])} tool call(s)",
        )
    ]
    for claim in result.rejected_claims:
        trace.append(
            _trace("fact_check", f"REJECTED {claim.claim_id}: {claim.rejection_reason}", "error")
        )

    out: dict[str, Any] = {"fact_check": result, "trace": trace}

    recommendation = state.get("recommendation")
    if recommendation is not None:
        verified_ids = {c.claim_id for c in result.verified_claims}
        out["recommendation"] = recommendation.model_copy(
            update={"actions": strip_unverified(recommendation.actions, verified_ids)}
        )

    if not result.passed:
        trace.append(
            _trace("fact_check", f"BLOCKED: {result.blocking_reason}", "error")
        )
        out["status"] = "blocked_by_factcheck"
    else:
        out["status"] = "awaiting_approval"
    return out
