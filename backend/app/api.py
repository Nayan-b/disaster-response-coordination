"""FastAPI service: SSE streaming of the agent trace + the human approval endpoint (Phase 5).

Threading model: the graph is synchronous and does genuinely blocking work — rasterio reads,
sync HTTP, SQLite checkpoint writes. Rather than pretend otherwise with an async checkpointer, each
run executes on a worker thread and pushes updates onto an asyncio queue that the SSE response
drains. Blocking work stays off the event loop, and the same code path works for Postgres.

The approval flow is deliberately two-endpoint:

    GET  /api/runs/{thread_id}/stream    → runs until the graph interrupts, streaming the trace
    POST /api/runs/{thread_id}/decision  → resumes the interrupted run with a human decision

Because the run lives in the checkpoint rather than in this process, the stream can drop, the browser
can close, and the pending approval is still there to be picked up later.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agents import llm
from app.agents.graph import build_graph, default_checkpointer
from app.models import AllocationAction, SourceUnavailable
from app.tools import ToolRunner

# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointer = default_checkpointer()
    app.state.checkpointer = checkpointer
    app.state.graph = build_graph(checkpointer)
    # Serializes graph execution per thread_id so two clients cannot advance the same run at once.
    app.state.locks: dict[str, threading.Lock] = {}
    yield


app = FastAPI(
    title="Flood Response Coordinator",
    version="0.1.0",
    description=(
        "Agentic flood-response coordination. All allocation output is a recommendation for human "
        "review; this service never executes a response action."
    ),
    lifespan=lifespan,
)

# Defaults cover the Next.js dev server; override with FLOOD_CORS_ORIGINS (comma-separated) when
# the dashboard is served from somewhere else. Deliberately not "*" — the approval endpoint mutates
# run state, so the allowed origins should be named.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "FLOOD_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _lock_for(thread_id: str) -> threading.Lock:
    locks: dict[str, threading.Lock] = app.state.locks
    return locks.setdefault(thread_id, threading.Lock())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    event_id: int | None = Field(None, description="Work a specific GDACS event; omit to triage.")
    episode_id: int | None = None
    lookback_days: int = 30
    alert_levels: list[str] = Field(default_factory=lambda: ["Orange", "Red"])
    reviewer: str = "responder"


class StartRunResponse(BaseModel):
    thread_id: str
    stream_url: str
    engine: str


class DecisionRequest(BaseModel):
    decision: Literal["approved", "edited", "rejected"]
    reviewer: str = "responder"
    notes: str | None = None
    edited_actions: list[AllocationAction] | None = None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Convert graph state into JSON, dropping nothing silently."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _public_state(values: dict[str, Any]) -> dict[str, Any]:
    """The state a client needs, excluding internals it should not depend on."""
    keys = (
        "event", "extent", "population", "imagery", "tiles", "candidate_events",
        "claims", "fact_check", "recommendation", "approval", "unavailable",
        "engine", "status", "event_id", "episode_id",
    )
    out = {k: _jsonable(values.get(k)) for k in keys}
    out["tool_calls"] = [
        {
            "call_id": c.call_id, "tool": c.tool, "arguments": _jsonable(c.arguments),
            "ok": c.ok, "called_at": c.called_at.isoformat(),
            "reason": c.result.get("reason") if not c.ok else None,
        }
        for c in (values.get("tool_calls") or [])
    ]
    return out


def _sse(event: str, data: Any) -> dict[str, str]:
    return {"event": event, "data": json.dumps(_jsonable(data))}


# Chrome withholds the start of a streamed response from `fetch` until its MIME-sniffing buffer
# fills (~1 KB). The early trace events are far smaller than that, so without padding the UI shows
# nothing for the first several seconds of a run — the stream is open and healthy, just invisible.
# An SSE comment is ignored by every client and flushes that buffer immediately.
_SNIFF_PADDING = {"comment": "padding " + "-" * 2048}

# Tell reverse proxies not to buffer the stream either (nginx and friends honor these).
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def _stream_graph(thread_id: str, graph_input: Any) -> AsyncIterator[dict[str, str]]:
    """Advance the graph on a worker thread, yielding SSE events as they arrive."""
    config = {"configurable": {"thread_id": thread_id}}
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def push(kind: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))

    def worker() -> None:
        lock = _lock_for(thread_id)
        if not lock.acquire(blocking=False):
            push("error", {"message": "this run is already being advanced by another client"})
            loop.call_soon_threadsafe(queue.put_nowait, None)
            return
        try:
            seen_trace = 0
            for chunk in app.state.graph.stream(
                graph_input, config, stream_mode="updates"
            ):
                for node, update in (chunk or {}).items():
                    if not isinstance(update, dict):
                        continue
                    # Trace lines are the human-readable reasoning stream.
                    for entry in update.get("trace") or []:
                        push("trace", entry)
                        seen_trace += 1
                    push("node_complete", {"node": node})
            del seen_trace
        except SourceUnavailable as exc:
            push("error", {"message": exc.reason, "source": exc.source})
        except Exception as exc:  # surface, don't swallow — the UI must not hang silently
            push("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            lock.release()
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, name=f"flood-run-{thread_id}", daemon=True).start()

    yield _SNIFF_PADDING
    yield _sse("run_started", {"thread_id": thread_id, "engine": llm.active_engine()})

    while True:
        item = await queue.get()
        if item is None:
            break
        kind, payload = item
        yield _sse(kind, payload)

    # Whatever the run did, report where it landed and hand over the full state.
    snapshot = app.state.graph.get_state(config)
    values = snapshot.values or {}
    state = _public_state(values)
    yield _sse("state", state)

    pending = _pending_interrupt(snapshot)
    if pending is not None:
        yield _sse("approval_request", pending)
    yield _sse("done", {"status": values.get("status"), "awaiting_approval": pending is not None})


def _pending_interrupt(snapshot: Any) -> dict[str, Any] | None:
    """The interrupt payload if the run is paused for a human, else None."""
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict):
                return value
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "engine": llm.active_engine(),
        "llm_configured": llm.llm_available(),
        "at": datetime.now(UTC).isoformat(),
    }


@app.get("/api/events")
def list_events(days: int = 30, levels: str = "Orange,Red") -> dict[str, Any]:
    """Flood events for the UI picker. Reports an empty result as data, not an error."""
    runner = ToolRunner()
    result = runner.invoke(
        "list_flood_events",
        {"days": days, "alert_levels": [x.strip() for x in levels.split(",") if x.strip()]},
    )
    if not result.get("ok"):
        return {"events": [], "unavailable": result.get("reason")}
    return {"events": result["events"], "source": result.get("source")}


@app.post("/api/runs", response_model=StartRunResponse)
def start_run(request: StartRunRequest) -> StartRunResponse:
    """Register a run. Streaming it is what actually advances the graph."""
    thread_id = f"run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    app.state.pending_inputs = getattr(app.state, "pending_inputs", {})
    app.state.pending_inputs[thread_id] = {
        "event_id": request.event_id,
        "episode_id": request.episode_id,
        "lookback_days": request.lookback_days,
        "alert_levels": request.alert_levels,
        "reviewer": request.reviewer,
    }
    return StartRunResponse(
        thread_id=thread_id,
        stream_url=f"/api/runs/{thread_id}/stream",
        engine=llm.active_engine(),
    )


@app.get("/api/runs/{thread_id}/stream")
async def stream_run(thread_id: str) -> EventSourceResponse:
    """Stream the run's trace over SSE, stopping when it interrupts for approval.

    Reconnect-safe: if the run already interrupted, this replays the state and the pending approval
    request instead of restarting the graph.
    """
    pending_inputs = getattr(app.state, "pending_inputs", {})
    graph_input = pending_inputs.pop(thread_id, None)

    if graph_input is None:
        snapshot = app.state.graph.get_state({"configurable": {"thread_id": thread_id}})
        if not snapshot.values:
            raise HTTPException(404, f"unknown run {thread_id!r}")
        # Nothing left to advance — replay current state rather than re-running the graph.
        return EventSourceResponse(_replay(thread_id, snapshot), headers=SSE_HEADERS)

    return EventSourceResponse(_stream_graph(thread_id, graph_input), headers=SSE_HEADERS)


async def _replay(thread_id: str, snapshot: Any) -> AsyncIterator[dict[str, str]]:
    values = snapshot.values or {}
    yield _SNIFF_PADDING
    yield _sse("run_started", {"thread_id": thread_id, "replay": True})
    for entry in values.get("trace") or []:
        yield _sse("trace", entry)
    yield _sse("state", _public_state(values))
    pending = _pending_interrupt(snapshot)
    if pending is not None:
        yield _sse("approval_request", pending)
    yield _sse("done", {"status": values.get("status"), "awaiting_approval": pending is not None})


@app.get("/api/runs/{thread_id}")
def get_run(thread_id: str) -> dict[str, Any]:
    snapshot = app.state.graph.get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot.values:
        raise HTTPException(404, f"unknown run {thread_id!r}")
    return {
        "thread_id": thread_id,
        "next": list(snapshot.next or ()),
        "awaiting_approval": _pending_interrupt(snapshot) is not None,
        "approval_request": _pending_interrupt(snapshot),
        "state": _public_state(snapshot.values),
        "trace": _jsonable(snapshot.values.get("trace") or []),
    }


@app.post("/api/runs/{thread_id}/decision")
async def submit_decision(thread_id: str, decision: DecisionRequest) -> dict[str, Any]:
    """Resume an interrupted run with the human's decision.

    Recording a decision is all this does — approval does not dispatch any action.
    """
    from langgraph.types import Command

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = app.state.graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(404, f"unknown run {thread_id!r}")
    if _pending_interrupt(snapshot) is None:
        raise HTTPException(
            409,
            f"run {thread_id!r} is not awaiting approval (status: {snapshot.values.get('status')})",
        )

    payload = decision.model_dump(mode="json")

    def resume() -> dict[str, Any]:
        lock = _lock_for(thread_id)
        with lock:
            return app.state.graph.invoke(Command(resume=payload), config)

    values = await asyncio.to_thread(resume)
    return {
        "thread_id": thread_id,
        "status": values.get("status"),
        "approval": _jsonable(values.get("approval")),
        "recommendation": _jsonable(values.get("recommendation")),
        "trace": _jsonable(values.get("trace") or [])[-4:],
        "notice": "Decision recorded. This system does not execute response actions.",
    }
