"""Shared state for the flood-response graph.

Kept as a TypedDict of plain/pydantic values so LangGraph can checkpoint it: a paused run must
survive the process exiting, which is what lets a responder close the tab and resume review later.

Note what is *not* here: the affected-area polygon. Geometry stays inside the tool layer (see
`app/tools.py`), so it never enters the checkpoint, the LLM context, or the SSE stream.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.models import (
    ApprovalDecision,
    Claim,
    FactCheckResult,
    Recommendation,
    ToolCall,
)


class TraceEvent(TypedDict):
    """One line of the agent's reasoning trace, streamed to the UI as it happens."""

    node: str
    message: str
    at: str
    level: str  # info | warning | error


class FloodState(TypedDict, total=False):
    # --- inputs ---
    event_id: int | None
    episode_id: int | None
    lookback_days: int
    alert_levels: list[str]
    reviewer: str

    # --- gathered facts ---
    event: dict[str, Any] | None
    extent: dict[str, Any] | None
    population: dict[str, Any] | None
    imagery: dict[str, Any] | None
    tiles: dict[str, Any] | None
    candidate_events: list[dict[str, Any]]

    # Appended to by every node, so the log is the full audit trail of the run.
    tool_calls: Annotated[list[ToolCall], operator.add]
    trace: Annotated[list[TraceEvent], operator.add]

    # Sources that failed, keyed by role, with the reason. Never silently empty.
    unavailable: dict[str, str]

    # --- synthesis and gating ---
    claims: list[Claim]
    fact_check: FactCheckResult | None
    recommendation: Recommendation | None
    approval: ApprovalDecision | None
    engine: str
    status: str
