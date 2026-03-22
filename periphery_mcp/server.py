"""Periphery MCP Server.

Exposes the Periphery intelligence platform to MCP-compatible AI clients
(Claude Desktop, Cursor, Zed, etc.) as a set of typed tools.

Run:
    python -m periphery_mcp.server          # stdio transport (default)
    python -m periphery_mcp.server --http   # HTTP/SSE transport on :8001

Configuration (environment variables):
    PERIPHERY_API_URL       Base URL of the Periphery backend  (default: http://127.0.0.1:8000)
    PERIPHERY_API_TOKEN     Bearer token for authenticated endpoints
    PERIPHERY_ADMIN_KEY     X-Admin-Key for admin endpoints
    PERIPHERY_API_TIMEOUT   Request timeout in seconds          (default: 30)
    PERIPHERY_MCP_PORT      HTTP transport port                 (default: 8001)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from periphery_mcp.client import PeripheryClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("periphery")
_client: PeripheryClient | None = None


def _get_client() -> PeripheryClient:
    global _client
    if _client is None:
        _client = PeripheryClient()
    return _client


def _ok(data: Any) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, indent=2, default=str))],
    )


def _err(msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {msg}")],
        isError=True,
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # ── Health & status ──────────────────────────────────────────────────
    Tool(
        name="periphery_health",
        description=(
            "Check the Periphery backend health: online status, vector count, "
            "cluster count, and last crystallization timestamp."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Analytical query ─────────────────────────────────────────────────
    Tool(
        name="periphery_query",
        description=(
            "Run a natural-language analytical query against Periphery. "
            "Returns entities, clusters, relationships, and a synthesised narrative answer. "
            "Use this as the primary tool for intelligence questions like "
            "'Who are the key actors connected to X?' or 'What clusters are emerging around Y?'"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question or analytical prompt.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session identifier for query history continuity.",
                    "default": "",
                },
                "confidence_threshold": {
                    "type": "number",
                    "description": "Minimum confidence score (0–1) for included results.",
                    "default": 0.0,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    ),

    # ── Semantic / vector search ─────────────────────────────────────────
    Tool(
        name="periphery_search",
        description=(
            "Full-text and semantic search across ingested documents. "
            "Returns matching document IDs, titles, snippets, and relevance scores. "
            "Use when you need raw document results rather than synthesised analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),

    # ── Snapshot overview ────────────────────────────────────────────────
    Tool(
        name="periphery_snapshot",
        description=(
            "Get the current ontology snapshot: top-level cluster summary, "
            "emerging themes, anomalies, and system legibility gradient. "
            "Use to orient yourself before asking specific questions."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Cluster list ─────────────────────────────────────────────────────
    Tool(
        name="periphery_clusters",
        description=(
            "List all active clusters in the current snapshot. "
            "Each cluster has an ID, label, document count, coherence score, "
            "and legibility tier (Solid → Defined → Emerging → Haze → Whisper)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_size": {
                    "type": "integer",
                    "description": "Minimum document count to include a cluster.",
                    "default": 1,
                },
            },
        },
    ),

    # ── Cluster detail ───────────────────────────────────────────────────
    Tool(
        name="periphery_cluster_detail",
        description=(
            "Get detailed information about a specific cluster: label, summary, "
            "member documents, related entities, relationships, and coherence metrics."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "cluster_id": {
                    "type": "string",
                    "description": "Cluster ID from periphery_clusters or periphery_snapshot.",
                },
            },
            "required": ["cluster_id"],
        },
    ),

    # ── Entity list ──────────────────────────────────────────────────────
    Tool(
        name="periphery_entities",
        description=(
            "List resolved entities (people, organisations, locations, etc.) "
            "extracted and cross-referenced across ingested documents."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Filter by type: PERSON, ORG, GPE, LOC, etc. Omit for all.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entities to return.",
                    "default": 50,
                },
            },
        },
    ),

    # ── Entity detail ────────────────────────────────────────────────────
    Tool(
        name="periphery_entity_detail",
        description=(
            "Get full profile for a specific entity: canonical name, aliases, "
            "mention count, source documents, related entities, and relationship network."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "canonical_id": {
                    "type": "string",
                    "description": "Entity canonical ID from periphery_entities or query results.",
                },
            },
            "required": ["canonical_id"],
        },
    ),

    # ── Relationships ────────────────────────────────────────────────────
    Tool(
        name="periphery_relationships",
        description=(
            "List extracted relationships between entities across the corpus. "
            "Each relationship has a type, source, target, confidence, and supporting evidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Filter to relationships involving this entity canonical ID.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum relationships to return.",
                    "default": 50,
                },
            },
        },
    ),

    # ── Emerging themes ──────────────────────────────────────────────────
    Tool(
        name="periphery_emerging",
        description=(
            "List emerging clusters — newly forming or rapidly growing themes "
            "detected by the crystallizer that haven't yet stabilised. "
            "Good for early-signal intelligence."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Anomalies ────────────────────────────────────────────────────────
    Tool(
        name="periphery_anomalies",
        description=(
            "List anomalous documents: items that don't fit any cluster "
            "(outliers in the embedding space). Often high-signal edge cases."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Trajectories ─────────────────────────────────────────────────────
    Tool(
        name="periphery_trajectories",
        description=(
            "List cluster trajectories — how clusters have evolved over time: "
            "growth, decay, splits, merges. Useful for trend analysis."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Critic scores ────────────────────────────────────────────────────
    Tool(
        name="periphery_critic_scores",
        description=(
            "Get Continuous Critic coherence scores for current clusters. "
            "Returns per-cluster confidence, ensemble breakdown, and drift metrics. "
            "Use to assess reliability of the current ontology."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Legibility gradient ──────────────────────────────────────────────
    Tool(
        name="periphery_legibility_gradient",
        description=(
            "Get the five-tier legibility gradient summary: "
            "Solid, Defined, Emerging, Haze, and Whisper tiers with cluster counts and confidence ranges. "
            "Gives a quick read on overall ontology maturity."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Ingest document ──────────────────────────────────────────────────
    Tool(
        name="periphery_ingest",
        description=(
            "Ingest a new document into Periphery for processing, embedding, "
            "enrichment, and clustering. Returns the assigned document ID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Document text content.",
                },
                "title": {
                    "type": "string",
                    "description": "Document title.",
                    "default": "",
                },
                "source": {
                    "type": "string",
                    "description": "Source identifier (URL, filename, feed name, etc.).",
                    "default": "",
                },
                "metadata": {
                    "type": "object",
                    "description": "Additional metadata key-value pairs.",
                    "default": {},
                },
            },
            "required": ["content"],
        },
    ),

    # ── Ingest stats ─────────────────────────────────────────────────────
    Tool(
        name="periphery_ingest_stats",
        description=(
            "Get ingest pipeline statistics: total documents, processing queue depth, "
            "enrichment backlog, and embedding coverage."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Pipeline status ──────────────────────────────────────────────────
    Tool(
        name="periphery_pipeline_status",
        description=(
            "Get multi-space embedding pipeline status: index sizes, last rebuild times, "
            "and coverage across semantic, entity, relational, temporal, and geospatial spaces."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # ── Query history ────────────────────────────────────────────────────
    Tool(
        name="periphery_query_history",
        description=(
            "Retrieve recent analytical query history with results summaries. "
            "Useful for review or to continue a prior analytical thread."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Filter to a specific session ID. Omit for all recent queries.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum queries to return.",
                    "default": 20,
                },
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool list handler
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


# ---------------------------------------------------------------------------
# Tool call dispatcher
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    client = _get_client()
    try:
        return await _dispatch(client, name, arguments)
    except Exception as exc:
        logger.exception("tool_call_failed name=%s", name)
        return _err(str(exc))


async def _dispatch(
    client: PeripheryClient,
    name: str,
    args: dict[str, Any],
) -> CallToolResult:

    # ── Health ───────────────────────────────────────────────────────────
    if name == "periphery_health":
        data = await client.get("/health")
        return _ok(data)

    # ── Analytical query ─────────────────────────────────────────────────
    elif name == "periphery_query":
        payload = {
            "query": args["query"],
            "session_id": args.get("session_id", ""),
            "confidence_threshold": args.get("confidence_threshold", 0.0),
            "max_results": args.get("max_results", 20),
        }
        data = await client.post("/api/query", payload)
        return _ok(data)

    # ── Search ───────────────────────────────────────────────────────────
    elif name == "periphery_search":
        payload = {
            "query": args["query"],
            "limit": args.get("limit", 10),
        }
        data = await client.post("/ingest/search", payload)
        return _ok(data)

    # ── Snapshot ─────────────────────────────────────────────────────────
    elif name == "periphery_snapshot":
        data = await client.get("/api/snapshot")
        return _ok(data)

    # ── Clusters ─────────────────────────────────────────────────────────
    elif name == "periphery_clusters":
        data = await client.get("/crystallizer/snapshot/clusters")
        min_size = args.get("min_size", 1)
        if isinstance(data, list):
            data = [c for c in data if c.get("size", c.get("doc_count", 0)) >= min_size]
        return _ok(data)

    # ── Cluster detail ───────────────────────────────────────────────────
    elif name == "periphery_cluster_detail":
        cluster_id = args["cluster_id"]
        data = await client.get(f"/api/cluster/{cluster_id}")
        return _ok(data)

    # ── Entities ─────────────────────────────────────────────────────────
    elif name == "periphery_entities":
        params: dict[str, Any] = {"limit": args.get("limit", 50)}
        if args.get("entity_type"):
            params["entity_type"] = args["entity_type"]
        data = await client.get("/api/entities", params=params)
        return _ok(data)

    # ── Entity detail ────────────────────────────────────────────────────
    elif name == "periphery_entity_detail":
        canonical_id = args["canonical_id"]
        data = await client.get(f"/api/entity/{canonical_id}")
        return _ok(data)

    # ── Relationships ────────────────────────────────────────────────────
    elif name == "periphery_relationships":
        params = {"limit": args.get("limit", 50)}
        if args.get("entity_id"):
            params["entity_id"] = args["entity_id"]
        data = await client.get("/api/relationships", params=params)
        return _ok(data)

    # ── Emerging ─────────────────────────────────────────────────────────
    elif name == "periphery_emerging":
        data = await client.get("/crystallizer/snapshot/emerging")
        return _ok(data)

    # ── Anomalies ────────────────────────────────────────────────────────
    elif name == "periphery_anomalies":
        data = await client.get("/crystallizer/snapshot/anomalies")
        return _ok(data)

    # ── Trajectories ─────────────────────────────────────────────────────
    elif name == "periphery_trajectories":
        data = await client.get("/crystallizer/snapshot/trajectories")
        return _ok(data)

    # ── Critic scores ────────────────────────────────────────────────────
    elif name == "periphery_critic_scores":
        data = await client.get("/critic/scores")
        return _ok(data)

    # ── Legibility gradient ──────────────────────────────────────────────
    elif name == "periphery_legibility_gradient":
        data = await client.get("/api/legibility-gradient")
        return _ok(data)

    # ── Ingest ───────────────────────────────────────────────────────────
    elif name == "periphery_ingest":
        payload = {
            "content": args["content"],
            "title": args.get("title", ""),
            "source": args.get("source", ""),
            "metadata": args.get("metadata", {}),
        }
        data = await client.post("/ingest/", payload)
        return _ok(data)

    # ── Ingest stats ─────────────────────────────────────────────────────
    elif name == "periphery_ingest_stats":
        data = await client.get("/ingest/stats")
        return _ok(data)

    # ── Pipeline status ──────────────────────────────────────────────────
    elif name == "periphery_pipeline_status":
        data = await client.get("/pipeline/status")
        return _ok(data)

    # ── Query history ────────────────────────────────────────────────────
    elif name == "periphery_query_history":
        params = {"limit": args.get("limit", 20)}
        if args.get("session_id"):
            params["session_id"] = args["session_id"]
        data = await client.get("/api/history", params=params)
        return _ok(data)

    else:
        return _err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    use_http = "--http" in sys.argv

    if use_http:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        import uvicorn

        port = int(os.environ.get("PERIPHERY_MCP_PORT", "8001"))
        sse = SseServerTransport("/messages/")

        async def handle_sse(request):  # type: ignore[no-untyped-def]
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await app.run(
                    streams[0],
                    streams[1],
                    app.create_initialization_options(),
                )

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )
        logger.info("Periphery MCP server listening on http://0.0.0.0:%d/sse", port)
        await uvicorn.Server(
            uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="warning")
        ).serve()
    else:
        logger.info("Periphery MCP server starting (stdio transport)")
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
