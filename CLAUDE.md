# Flood Response Coordinator

Multi-agent flood-response coordination: live disaster data → drafted resource recommendation →
**human approval gate**. Nothing in this system executes a response action; approval records a
decision.

## Commands

All backend commands run from `backend/` (uv manages the venv — `uv run` from the repo root picks up
the wrong Python).

```bash
cd backend
uv sync --extra dev

uv run python -m app.cli events --days 45          # live GDACS flood events
uv run python -m app.cli report --event-id <id>    # all three sources + provenance
uv run python -m app.cli cache                     # cache size by source

uv run pytest                                      # 108 offline tests (committed fixtures)
uv run pytest -m live                              # 9 tests against the real APIs

uv run uvicorn app.api:app --port 8088 --reload    # API (see port note below)
uv run python -m app.mcp_server                    # MCP server over stdio
```

```bash
cd frontend
npm run dev        # Next.js on :3000
npx tsc --noEmit   # typecheck
npm run build
```

```bash
docker compose up --build      # whole stack: postgres + backend + frontend
docker compose logs -f backend
docker compose down -v         # stop and wipe volumes
```

**Port 8088, not 8000.** Port 8000 is occupied by an unrelated Django project on this machine. The
frontend reads `NEXT_PUBLIC_API_BASE` from `frontend/.env.local`.

## Architecture

```
GDACS / CMR-STAC / GIBS / WorldPop      ← app/sources/*  (Phase 1, no LLM)
                 ↓
          app/tools.py                   ← one registry of capabilities
           ↙            ↘
  app/mcp_server.py   app/agents/        ← MCP adapter | LangGraph nodes
                          ↓
  alert_monitor → imagery_retrieval → population_overlay → allocation_draft
                          ↓
                     fact_check  ──(fails)──→ action_log   (never reaches a human)
                          ↓
                     human_gate  ← interrupt()
                          ↓
                     action_log
                 ↓
      app/api.py (FastAPI + SSE)  →  frontend/ (Next.js, Leaflet, shadcn)
```

## Non-obvious rules

These exist because of specific failures found by probing the live APIs. Changing them silently
breaks correctness.

**Every number must trace to a tool call.** `ToolRunner` logs each call as a `ToolCall`; the
Allocation agent may only cite claim ids built from those results. Don't add a code path that puts a
figure in a recommendation without a `source_call_id`.

**The Fact-Check agent is deterministic code (`app/agents/factcheck.py`), never a prompt.** Its three
rules are enforced, not requested: a numeric claim must cite a tool call from this run, that call
must have succeeded, and the number must actually appear in its result. An unavailable source cannot
be claimed about at all. Do not "improve" this by delegating verification to a model.

**Geometry never crosses the model boundary.** A GDACS affected-area polygon is ~880 KB. Tools take
`event_id`/`episode_id` and resolve geometry server-side; `get_flood_extent` returns bbox + area, not
coordinates. This keeps the polygon out of the LLM context, the checkpoint, and the SSE stream.

**Missing data is an error, never an estimate.** Sources raise `SourceUnavailable`; tools return
`{ok: false, unavailable: true, reason}`. Nothing interpolates or substitutes a fallback number.

**Data-source traps** (each pinned by a test — see `backend/README.md` for the full table):

- WorldPop must use the **UN-adjusted** product. The unconstrained one overstates by a
  country-dependent margin (+38% Nepal, +5% Pakistan).
- Population is a **sum**, not a mean — pixels are people-per-pixel. Nodata is `-99999.0`; a naive
  sum over Nepal gives `-2.8e10`.
- The GDACS **event-list bbox is a zero-area centroid**. `FloodEvent` deliberately has no bbox field;
  extents come from the geometry endpoint's `Poly_Affected` polygon.
- Select by property, not position: GDACS returns the centroid and a much larger `Poly_Global`
  alongside `Poly_Affected`; CMR-STAC asset keys are dynamic production timestamps, so data assets
  are matched by href extension.
- CMR's NRT archive is only days deep — historical queries returning zero is correct. GIBS tiles
  (2021→present, no auth) are the historical path.
- WorldPop's async stats API never completes; `files[0]` is not always a raster.

## LLM providers (`app/agents/llm.py`)

Three engines behind one interface: Anthropic direct, OpenRouter, and the rule-based fallback.
`active_provider()` resolves which — `FLOOD_LLM_PROVIDER` wins, else `ANTHROPIC_API_KEY` beats
`OPENROUTER_API_KEY`, else rule-based.

**Anthropic (`claude-opus-5`):**

- **Never send `temperature`, `top_p`, or `top_k`** — Opus 5 returns a 400.
- Thinking is on by default and shares the `max_tokens` budget, so keep it generous.
- Structured output uses Anthropic's native `output_config.format`, **not** LangChain's
  `with_structured_output()` (which forces `tool_choice`, whose interaction with thinking is not
  worth gambling on).

**OpenRouter** (OpenAI-compatible gateway, model ids vendor-namespaced):

- **`use_responses_api=False` is required.** langchain-openai 1.x targets OpenAI's Responses API by
  default; OpenRouter serves `/chat/completions` only, and the mismatch fails while *parsing a valid
  reply* (`'NoneType' object is not iterable`), which reads like a server fault.
- **`reasoning` must go in `extra_body`, not the `reasoning=` field.** As a named kwarg the
  structured-output `.parse()` path rejects it outright.
- Structured output is `response_format: {type: "json_schema", strict: true}` — the OpenAI dialect.
  `_schema_for()` emits a schema valid for both providers.
- The token budget goes out as `max_completion_tokens` (langchain-openai aliases it); OpenRouter
  accepts it.
- Don't send `temperature` here either, even though OpenRouter advertises it for Opus 5 — keeping
  both paths prompt-steered means switching providers doesn't change behavior.

**Provenance:** OpenRouter can route to a different upstream than requested, so `drafted_by` comes
from the response's model field (`engine_label_for()`), not the requested id. Don't "simplify" that
back to the configured model.

**The rule-based fallback is load-bearing.** With no Anthropic credentials the graph produces the
same structured objects from the same evidence, so the full pipeline including the approval gate
stays runnable and testable. Every recommendation records `drafted_by` (`claude-opus-5` or
`rule-based`) and the UI shows it. Keep both paths working.

## Frontend notes

- **Leaflet, not MapLibre GL.** MapLibre 5 and 6 both failed to initialize their style here — canvas
  sized correctly, `getStyle()` null, zero tile requests, no error event — with WebGL2, Workers, and
  tile-host reachability separately confirmed working. Everything this map needs is raster, so
  Leaflet's `<img>` tiles are both sufficient and far less fragile.
- `FloodMap` is loaded via `dynamic(..., { ssr: false })`; map libraries must not run in the SSR pass.
- SSE is consumed with `fetch` + a manual parse, not `EventSource` (which can't set headers, abort
  cleanly, or expose HTTP status).
- Next.js 16 + Tailwind v4 + React 19. `frontend/AGENTS.md` is generated by `next dev` — commit it
  rather than deleting it.

**Console layout.** Events rail | map | tabbed detail panel (Brief · Situation · Evidence · Trace),
with `WorkflowStepper` across the top. The stepper is driven by the stream's `node_complete` frames —
which the graph emits for every node *except* `human_gate`, since that one interrupts rather than
returning. So the gate's "awaiting" state comes from the `approval_request` frame, and `action_log`
stays pending until a decision resumes the run. `lib/workflow.ts` owns that derivation; a stage the
graph routed around renders as *skipped*, never as done.

**The panels never invent a value.** `lib/format.ts` renders missing data as an em dash, and an
unavailable source prints its reason — the frontend counterpart of "missing data is an error, never
an estimate". A partial population total keeps its PARTIAL label and names the missing raster.

**Editing at the gate is per-action.** The old free-text editor re-paired edited lines with the
original rationale and claim ids *by list position*, so deleting one line silently re-attributed
every citation below it. Each action now carries its own rationale and `supporting_claim_ids`
through the edit. Don't collapse it back to a textarea.

**The decision endpoint returns the run's last four trace lines, not just new ones** — `page.tsx`
dedupes on `node@at` before appending, or the tail of the trace renders twice.

## Checkpointing

SQLite locally (`data/checkpoints.sqlite`), Postgres when `FLOOD_POSTGRES_DSN` is set. The msgpack
allowlist in `app/agents/graph.py` (`CHECKPOINTED_TYPES`) must list every pydantic model that reaches
graph state — LangGraph warns now and will block unregistered types later, and a permissive
deserializer on a checkpoint DB is a code-execution path. Add new state models there.

`postgres_checkpointer()` builds a `PostgresSaver` from an explicit `ConnectionPool` rather than
`PostgresSaver.from_conn_string()`, because that helper takes no `serde` argument (so the allowlist
could not be applied) and yields a context manager, which doesn't suit a long-lived server. The pool
requires `autocommit=True` and `dict_row`.

## Docker

- **`libexpat1` is required in the backend image.** rasterio's wheel bundles GDAL but not expat, so
  `import rasterio` fails on `python:*-slim` without it. Confirmed by `ldd` as the only missing
  library — don't apt-install a system GDAL, which would risk two GDAL builds disagreeing.
- **`NEXT_PUBLIC_API_BASE` is a frontend *build* arg, not a runtime env var.** It is inlined into the
  browser bundle, so it must be the host-reachable URL (`http://localhost:8088`), never the
  compose-internal `backend:8088`. Changing the backend port means rebuilding the frontend image.
- The Postgres healthcheck passes `-d $POSTGRES_DB`; without it `pg_isready` probes a database named
  after the user and reports ready before ours exists.
- One uvicorn worker by design — the per-run lock preventing two clients advancing the same run is
  process-local. Scaling out means moving that lock into Postgres first.

## Status

Phases 1–6 are built and verified against live APIs. Remaining: the historical replay mode.

The browser SSE issue is resolved: sse-starlette separates frames with CRLFCRLF and the client split
on `"\n\n"`, so no event ever dispatched; Chrome also withheld the first ~1 KB of the stream for MIME
sniffing. Both are fixed (`/\r?\n\r?\n/` split, plus a padding comment) — don't reintroduce either.

The approval card's click-through is now verified in a real browser (headless Chrome over CDP, live
GDACS event 1104051): rail click → streamed trace → gate → Approve → `logged_approved` receipt, with
an empty console. What remains unverified end-to-end in a browser is the **edit** path and the
**blocked-by-factcheck** path; both are covered at the API level only.
