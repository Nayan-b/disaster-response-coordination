"""MCP server exposing the flood-response tools (Phase 2).

Thin adapter only: every tool delegates to `app.tools.REGISTRY`, so the MCP surface and the
in-process agent surface can never drift apart. Schemas are derived from these wrappers' type
annotations, and the descriptions come from the registry so there is one canonical wording.

Run it:
    uv run python -m app.mcp_server              # stdio (what MCP clients expect)
    uv run python -m app.mcp_server --http       # streamable HTTP on :8765

Tools deliberately take an `event_id` rather than geometry — see the module docstring in
`app/tools.py` for why coordinates never cross the model boundary.
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from app import config
from app.tools import REGISTRY, ToolRunner

server = MCPServer(
    name="flood-response-coordinator",
    version="0.1.0",
    instructions=(
        "Tools for coordinating flood disaster response from live data: GDACS event alerts, NASA "
        "flood imagery, and WorldPop population estimates.\n\n"
        "Call list_flood_events first to discover events, then use the returned event_id with the "
        "other tools. Never invent coordinates — the tools resolve geometry themselves.\n\n"
        "Any tool may return {ok: false, unavailable: true, reason: ...}. That is a real answer: "
        "report the gap to the user rather than estimating a substitute value. Every number you "
        "state must come from a tool result in this session."
    ),
)

# One runner per process so the MCP session keeps an auditable call log alongside the responses.
_runner = ToolRunner()


def _run(name: str, **arguments: Any) -> dict[str, Any]:
    return _runner.invoke(name, {k: v for k, v in arguments.items() if v is not None})


def list_flood_events(days: int = 30, alert_levels: list[str] | None = None) -> dict[str, Any]:
    return _run("list_flood_events", days=days, alert_levels=alert_levels)


def get_flood_extent(event_id: int, episode_id: int | None = None) -> dict[str, Any]:
    return _run("get_flood_extent", event_id=event_id, episode_id=episode_id)


def estimate_population(
    event_id: int,
    episode_id: int | None = None,
    year: int = config.WORLDPOP_DEFAULT_YEAR,
) -> dict[str, Any]:
    return _run("estimate_population", event_id=event_id, episode_id=episode_id, year=year)


def search_flood_imagery(
    event_id: int, episode_id: int | None = None, days: int = 7
) -> dict[str, Any]:
    return _run("search_flood_imagery", event_id=event_id, episode_id=episode_id, days=days)


def get_tile_layer(event_id: int, episode_id: int | None = None) -> dict[str, Any]:
    return _run("get_tile_layer", event_id=event_id, episode_id=episode_id)


_WRAPPERS = {
    "list_flood_events": list_flood_events,
    "get_flood_extent": get_flood_extent,
    "estimate_population": estimate_population,
    "search_flood_imagery": search_flood_imagery,
    "get_tile_layer": get_tile_layer,
}


def register_tools(target: MCPServer = server) -> MCPServer:
    """Register every registry tool, taking each description from the registry itself."""
    for name, wrapper in _WRAPPERS.items():
        spec = REGISTRY[name]
        target.add_tool(wrapper, name=spec.name, description=spec.description)
    return target


register_tools()


def main() -> None:
    if "--http" in sys.argv:
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
