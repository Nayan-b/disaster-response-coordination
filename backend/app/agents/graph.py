"""LangGraph wiring: Phase 3 orchestration + Phase 4 human-in-the-loop and checkpointing.

    alert_monitor → imagery_retrieval → population_overlay → allocation_draft
                                                                    ↓
                                                              fact_check
                                                                ↓      ↓
                                                    (blocked) ←╯      human_gate  ← interrupt()
                                                                          ↓
                                                                     action_log

The gate is a real `interrupt()`, not a prompt asking the model to wait: the graph *stops*, the
checkpointer persists the state, and nothing continues until a human resumes it with a decision.
That is what makes "a responder can close the tab and resume review later" true — the run lives in
the checkpoint, not in a process.

`fact_check` routes to the gate only when verification passed. A draft whose load-bearing figure
failed verification never reaches a human, because a recommendation with a hole in it is worse than
no recommendation.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import config
from app.agents.nodes import (
    alert_monitor,
    allocation_draft,
    fact_check,
    imagery_retrieval,
    population_overlay,
)
from app.agents.state import FloodState, TraceEvent
from app.models import AllocationAction, ApprovalDecision

CHECKPOINT_PATH = Path(
    os.environ.get("FLOOD_CHECKPOINT_DB", config.REPO_ROOT / "data" / "checkpoints.sqlite")
)


def _trace(node: str, message: str, level: str = "info") -> TraceEvent:
    return TraceEvent(node=node, message=message, at=datetime.now(UTC).isoformat(), level=level)


# ---------------------------------------------------------------------------
# Human gate
# ---------------------------------------------------------------------------


def human_gate(state: FloodState) -> dict[str, Any]:
    """Pause for a human decision on the recommendation.

    `interrupt()` surfaces the payload to whoever is driving the graph and suspends the run. The
    value passed to `Command(resume=...)` comes back as this call's return value.

    Accepted resume payloads:
        {"decision": "approved", "reviewer": "..."} — endorse as drafted
        {"decision": "edited", "reviewer": "...", "edited_actions": [...]} — replace the actions
        {"decision": "rejected", "reviewer": "...", "notes": "..."} — discard
    """
    recommendation = state.get("recommendation")
    fact = state.get("fact_check")

    decision_payload = interrupt(
        {
            "type": "approval_request",
            "event": state.get("event"),
            "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
            "verified_claims": [c.model_dump(mode="json") for c in (fact.verified_claims if fact else [])],
            "rejected_claims": [c.model_dump(mode="json") for c in (fact.rejected_claims if fact else [])],
            "unavailable": state.get("unavailable") or {},
            "drafted_by": state.get("engine"),
            # Restated at the gate so an approving human is never in doubt about what they are
            # endorsing: their approval records a decision, it does not dispatch anything.
            "notice": (
                "This is a recommendation for human review. Approving records your decision; it "
                "does not execute any action."
            ),
        }
    )

    payload = decision_payload if isinstance(decision_payload, dict) else {"decision": str(decision_payload)}
    decision = str(payload.get("decision", "rejected")).lower()
    if decision not in {"approved", "edited", "rejected"}:
        decision = "rejected"

    edited_actions = None
    if payload.get("edited_actions"):
        edited_actions = [
            a if isinstance(a, AllocationAction) else AllocationAction.model_validate(a)
            for a in payload["edited_actions"]
        ]

    approval = ApprovalDecision(
        decision=decision,  # type: ignore[arg-type]
        reviewer=str(payload.get("reviewer") or state.get("reviewer") or "unknown"),
        decided_at=datetime.now(UTC),
        notes=payload.get("notes"),
        edited_actions=edited_actions,
    )

    out: dict[str, Any] = {
        "approval": approval,
        "trace": [
            _trace(
                "human_gate",
                f"Human {approval.decision} by {approval.reviewer}"
                + (f": {approval.notes}" if approval.notes else ""),
                "info" if approval.decision != "rejected" else "warning",
            )
        ],
        "status": approval.decision,
    }

    # An edit replaces the actions but keeps the drafting engine on record, so the log shows both
    # who drafted and who changed it.
    if approval.decision == "edited" and edited_actions and recommendation is not None:
        out["recommendation"] = recommendation.model_copy(update={"actions": edited_actions})
    return out


def action_log(state: FloodState) -> dict[str, Any]:
    """Final node: record the outcome. The checkpointer is the durable store."""
    approval = state.get("approval")
    recommendation = state.get("recommendation")
    summary = "no recommendation"
    if recommendation is not None:
        summary = f"{len(recommendation.actions)} action(s) — {recommendation.headline}"
    return {
        "trace": [
            _trace(
                "action_log",
                f"Logged outcome: {approval.decision if approval else 'blocked'} · {summary}",
            )
        ],
        "status": f"logged_{approval.decision}" if approval else "logged_blocked",
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _after_alert(state: FloodState) -> Literal["imagery_retrieval", "action_log"]:
    return "imagery_retrieval" if state.get("event") else "action_log"


def _after_factcheck(state: FloodState) -> Literal["human_gate", "action_log"]:
    """Only a draft that survived verification reaches a human."""
    result = state.get("fact_check")
    if result is not None and not result.passed:
        return "action_log"
    return "human_gate"


def build_graph(checkpointer: Any | None = None):
    """Compile the graph. Pass a checkpointer to make runs resumable."""
    graph = StateGraph(FloodState)

    graph.add_node("alert_monitor", alert_monitor)
    graph.add_node("imagery_retrieval", imagery_retrieval)
    graph.add_node("population_overlay", population_overlay)
    graph.add_node("allocation_draft", allocation_draft)
    graph.add_node("fact_check", fact_check)
    graph.add_node("human_gate", human_gate)
    graph.add_node("action_log", action_log)

    graph.add_edge(START, "alert_monitor")
    graph.add_conditional_edges("alert_monitor", _after_alert)
    graph.add_edge("imagery_retrieval", "population_overlay")
    graph.add_edge("population_overlay", "allocation_draft")
    graph.add_edge("allocation_draft", "fact_check")
    graph.add_conditional_edges("fact_check", _after_factcheck)
    graph.add_edge("human_gate", "action_log")
    graph.add_edge("action_log", END)

    return graph.compile(checkpointer=checkpointer)


# The state we checkpoint contains our own pydantic models. LangGraph's serializer allows any type
# by default but warns and will block unregistered ones in a future release, so the allowlist is
# declared explicitly. It is also the safer setting: a permissive deserializer on a checkpoint
# database is a code-execution path for anyone who can write to that database.
CHECKPOINTED_TYPES = (
    ("app.models", "ToolCall"),
    ("app.models", "Claim"),
    ("app.models", "FactCheckResult"),
    ("app.models", "Recommendation"),
    ("app.models", "AllocationAction"),
    ("app.models", "ApprovalDecision"),
    ("app.models", "Provenance"),
)


def make_serializer():
    """Serializer with our checkpointed model types explicitly allowlisted."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=list(CHECKPOINTED_TYPES))


def sqlite_checkpointer(path: Path | str = CHECKPOINT_PATH):
    """A SqliteSaver for local development.

    `check_same_thread=False` because FastAPI serves requests across threads. PostgresSaver is the
    production path — see `postgres_checkpointer()` below.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from langgraph.checkpoint.sqlite import SqliteSaver

    connection = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(connection, serde=make_serializer())


def postgres_checkpointer(dsn: str | None = None):
    """A PostgresSaver for production (used by docker-compose).

    Returns a context manager — `PostgresSaver.from_conn_string()` owns the connection pool, and
    `.setup()` must run once to create the checkpoint tables.
    """
    dsn = dsn or os.environ.get("FLOOD_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("set FLOOD_POSTGRES_DSN to use the Postgres checkpointer")
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver.from_conn_string(dsn, serde=make_serializer())


def default_checkpointer():
    """Postgres when a DSN is configured, else local SQLite."""
    if os.environ.get("FLOOD_POSTGRES_DSN"):
        saver = postgres_checkpointer().__enter__()
        saver.setup()
        return saver
    return sqlite_checkpointer()
