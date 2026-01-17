# Flood Response Coordinator

A multi-agent system that ingests live flood-disaster alerts, pulls satellite imagery, overlays
population density, and drafts resource-allocation recommendations — **every one of which a human
responder must approve before it is finalized.**

Nothing here executes a response action. Approving a recommendation records a decision; it does not
dispatch anything.

---

## Why this exists

Disaster-response coordination is still largely manual: a human cross-references satellite imagery,
GDACS alerts, and population data across separate browser tabs, then makes a judgment call.

This project automates the data-gathering and first-pass synthesis while keeping a human firmly in
control of any allocation decision — because a hallucinated or overconfident number in this domain
has real consequences. That tension (automate the tedious 90%, gate the consequential 10% behind
human approval) is the point of the project, and it is the same tension real humanitarian-tech teams
grapple with.

The design consequence: **the guardrail is mechanical, not aspirational.** Every figure that reaches
a responder must be traceable to a specific tool call, and a deterministic Fact-Check stage removes
any claim that isn't — before a human ever sees it.

---

## What it does

```
GDACS alerts ─┐
NASA CMR-STAC ├─→ tools ─→ LangGraph agents ─→ fact-check ─→ HUMAN APPROVAL ─→ action log
NASA GIBS     │                                    │              (approve /
WorldPop     ─┘                          (removes unverifiable      edit /
                                          claims; can block)        reject)
```

1. **Alert Monitor** queries GDACS for active flood events above a severity threshold and picks the
   most urgent.
2. **Imagery Retrieval** derives the affected-area extent, searches NASA CMR-STAC for flood granules,
   and builds a GIBS tile layer for the map.
3. **Population Overlay** sums WorldPop population inside the actual flood polygon.
4. **Allocation Draft** writes a prioritized recommendation citing only evidence it was given.
5. **Fact-Check** verifies every claim against the tool-call log and strips or blocks what it can't
   verify.
6. **Human gate** pauses the graph via LangGraph `interrupt()`. The run is checkpointed, so a
   responder can close the tab and resume review later.

---

## Data sources

All live, all free, no keys required for what this app does.

| Source | Provides | Notes |
|---|---|---|
| **GDACS** (UN + EU JRC) | Active flood events, alert levels, affected-area polygons | No key |
| **NASA CMR-STAC** (`LANCEMODIS`) | MODIS/VIIRS flood-product granule references | Earthdata login needed only to *download* granules |
| **NASA GIBS** | Pre-rendered flood imagery tiles, 2021→present | No auth; this is what the map displays |
| **WorldPop** | 1 km UN-adjusted population rasters | Cached to disk, summed locally |

---

## Quick start (Docker)

The whole stack — Postgres, backend, dashboard — in one command:

```bash
docker compose up --build     # → http://localhost:3000
```

Compose starts Postgres first and waits for it to be genuinely ready, brings up the backend, waits
for its health check, then starts the dashboard. No key is required; without one the stack runs on
the rule-based engine. To use Claude, `cp .env.example .env` and set `ANTHROPIC_API_KEY`.

| Service | Port | Notes |
|---|---|---|
| `frontend` | 3000 | Next.js dashboard |
| `backend` | 8088 | FastAPI + SSE |
| `postgres` | 55432 | Checkpoint store, exposed for `psql` |

Two named volumes persist across restarts: `postgres-data` (paused runs) and `flood-cache`
(downloaded rasters — worth keeping, since WorldPop rasters run ~100 MB per country).

```bash
docker compose logs -f backend      # follow the backend
docker compose down                 # stop, keep data
docker compose down -v              # stop and wipe volumes
```

> **If you change `BACKEND_PORT`, rebuild the frontend** (`docker compose up --build`).
> `NEXT_PUBLIC_API_BASE` is inlined into the browser bundle at build time, not read at runtime.

To seed the container's cache from a local one (handy for offline demos, or when an upstream host
is down):

```bash
docker compose cp data/cache/worldpop/. backend:/data/cache/worldpop/
docker compose exec -u root backend chown -R flood:flood /data/cache
```

## Running locally without Docker

Requires Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
# Backend — run from backend/, not the repo root
cd backend
uv sync --extra dev
uv run uvicorn app.api:app --port 8088 --reload
```

```bash
# Frontend, in a second terminal
cd frontend
npm install
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8088' > .env.local
npm run dev            # → http://localhost:3000
```

Open http://localhost:3000, pick a flood event, and watch the agents work. When the run pauses, the
approval card appears.

> **Port 8088, not 8000** — port 8000 is occupied by an unrelated service on the original dev
> machine. Change it freely; just keep `NEXT_PUBLIC_API_BASE` in sync.

### Without the frontend

```bash
cd backend
uv run python -m app.cli events --days 45           # live GDACS events
uv run python -m app.cli report --event-id 1103933  # all sources + provenance table
uv run python -m app.cli cache                      # cached data by source
```

### As an MCP server

The same tools are exposed over MCP for any MCP client:

```bash
cd backend
uv run python -m app.mcp_server        # stdio
uv run python -m app.mcp_server --http # streamable HTTP
```

---

## LLM providers

Three engines, one interface. The system runs on any of them:

| Provider | Configure with | `drafted_by` shows |
|---|---|---|
| **Anthropic** (direct) | `ANTHROPIC_API_KEY` | `claude-opus-5` |
| **OpenRouter** (400+ models) | `OPENROUTER_API_KEY` | `openrouter:anthropic/claude-opus-5` |
| **None** — deterministic | *(nothing)* | `rule-based` |

```bash
export ANTHROPIC_API_KEY=sk-ant-...           # Anthropic direct
export OPENROUTER_API_KEY=sk-or-...           # or via OpenRouter
export FLOOD_OPENROUTER_MODEL=openai/gpt-5.2  # any OpenRouter model id
```

With both keys set, the direct Anthropic path wins — fewer hops. Force the gateway with
`FLOOD_LLM_PROVIDER=openrouter`, or pin the deterministic engine with `FLOOD_LLM_PROVIDER=none`
even when keys are present.

OpenRouter model ids are vendor-namespaced (`anthropic/claude-opus-5`, `openai/gpt-5.2`,
`google/gemini-2.5-pro`). **Pick one that supports structured outputs** — drafting needs
schema-constrained JSON, and roughly 335 of OpenRouter's ~413 models qualify.

The rule-based path is a real fallback, not a stub: it produces the same structured objects from
the same tool results, so the entire pipeline — fact-check and approval gate included — is runnable
and testable with no key at all. It is also what the system falls back to if a configured provider
errors mid-run, with the reason recorded as a caveat rather than silently swallowed.

**Provenance survives the gateway.** OpenRouter may route a request to a different upstream than the
one requested — that is the point of it — so `drafted_by` reports the model that *actually answered*,
read from the response, not the one that was asked for. In a system where every number is traceable,
"which model wrote this" shouldn't be a guess.

Two provider-specific details worth knowing, both in `app/agents/llm.py`:

- **Structured output differs by dialect.** Anthropic uses its native `output_config.format`;
  OpenRouter speaks the OpenAI-compatible `response_format: {type: "json_schema", strict: true}`.
  One pydantic schema feeds both.
- **Neither path sends `temperature`.** Claude Opus 5 rejects it with a 400. OpenRouter advertises
  it for the same model because it normalizes across vendors — but keeping both prompt-steered means
  switching providers doesn't quietly change behavior.

## The guardrail

This is the part worth reading the code for. Three rules, enforced in
`backend/app/agents/factcheck.py`:

1. **A numeric claim must cite a tool call** from the current run.
2. **That call must have succeeded.**
3. **The number must actually appear in that call's result.**

A source that was unavailable cannot be claimed about at all — if population lookup failed, no
population figure may be asserted, and the recommendation says the data is missing.

**The Fact-Check stage is deterministic code, never a prompt.** Asking a language model to verify
another language model's numbers reintroduces the exact failure it exists to catch: a confident,
plausible, unsourced figure. Verification here is lookup and arithmetic.

Tested adversarially — it accepts an honest figure and a rounded one, and rejects all four attack
shapes, including the subtle one:

| Claim | Verdict |
|---|---|
| `2,063,552 people`, cites the real population call | accepted |
| `~2.06M people` (rounded within 0.5%) | accepted |
| `4,500,000 people`, citing a **real** call that never produced that number | **rejected** |
| A number with no citation | **rejected** |
| Cites a call that doesn't exist | **rejected** |
| Cites a call that **failed** | **rejected** |

When a load-bearing claim is rejected, the draft is **blocked from reaching the human** — a
recommendation with a hole in it is worse than no recommendation.

---

## Project layout

```
backend/
  app/
    sources/       GDACS, CMR-STAC/GIBS, WorldPop clients (no LLM)
    agents/        LangGraph nodes, deterministic fact-check, Claude access
    tools.py       one registry of capabilities, shared by MCP and the agents
    mcp_server.py  MCP adapter over that registry
    api.py         FastAPI + SSE streaming + approval endpoint
    cli.py         inspect any source from the terminal
  tests/           84 offline (committed fixtures) + 9 live
frontend/
  app/page.tsx     map-first dashboard
  components/      Leaflet map, trace panel, approval card
  lib/api.ts       SSE client
```

---

## Design decisions

**Geometry never reaches the model.** A GDACS affected-area polygon is ~880 KB of coordinates.
Passing it through an LLM would blow the context window and invite the model to fabricate or truncate
coordinates. Tools take an `event_id` and resolve geometry server-side; the model names an event but
never handles spatial data.

**Missing data is an error, never an estimate.** Sources raise `SourceUnavailable`; tools return
`{ok: false, reason: ...}`. Nothing interpolates or substitutes a fallback value, and the UI shows
what was unavailable and why.

**The data-gathering agents are deterministic; the LLM is reserved for judgment.** Extent → population
→ imagery is a fixed correct sequence, so handing it to a model would add latency, cost, and a
fabrication surface for no benefit.

**Runs live in a checkpoint, not in a process.** SQLite locally, Postgres under compose. A paused run
survives a server restart — verified by restarting the backend container mid-approval and then
approving the recovered run — which is what makes "close the tab and come back" true rather than
aspirational.

---

## Testing

```bash
cd backend
uv run pytest           # 84 offline tests, no network
uv run pytest -m live   # 9 tests against the real APIs
```

Fixtures are deliberately hostile — hostile feature ordering, a zip archive listed before the raster,
dynamic asset keys — so a regression to a naive implementation can't pass.

The live tests are the early-warning system for upstream API changes; two of them are documentary,
and will fail if GDACS or NASA *improve* their behavior, signaling that a workaround can be removed.

---

## Data quirks worth knowing

Each was found by probing the live APIs, is handled in code, and is pinned by a test. Full detail in
[`backend/README.md`](backend/README.md).

- **WorldPop must be UN-adjusted.** The unconstrained product overstates by a country-dependent
  margin (+38% Nepal, +5% Pakistan) — enough to badly mis-scale a relief estimate.
- **Population is a sum, not a mean** (pixels are people-per-pixel), and nodata is `-99999.0` — a
  naive sum over Nepal gives `-2.8e10`.
- **The GDACS event-list bbox is a zero-area centroid.** Real extents come from the geometry endpoint.
- **CMR's near-real-time archive is only days deep**, so historical queries returning nothing is
  correct behavior; GIBS covers 2021→present.

---

## Status

Phases 1–6 complete and verified against live APIs: data layer, MCP tools, agent graph,
human-in-the-loop with checkpointing, the streaming map UI, and Docker Compose orchestration with
Postgres.

Not yet built: historical replay mode for demos.

One caveat on verification: the approval card's click-through in the browser hasn't been exercised
end to end. The SSE transport and the approval endpoint are both verified independently (via a Node
client against the live stream, and direct API calls against the containerized stack), but the final
UI click path is unconfirmed.
