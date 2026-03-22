# Periphery MCP Server

Exposes the [Periphery](https://github.com/nadjabynadja/Periphery) intelligence platform as an [MCP](https://modelcontextprotocol.io/) server, enabling AI clients (Claude Desktop, Cursor, Zed, etc.) to query, search, and ingest documents directly.

## Tools

| Tool | Description |
|------|-------------|
| `periphery_health` | Backend health: status, vector count, cluster count |
| `periphery_query` | Natural-language analytical query → entities, clusters, synthesised narrative |
| `periphery_search` | Full-text / semantic search across ingested documents |
| `periphery_snapshot` | Current ontology snapshot overview |
| `periphery_clusters` | List all clusters with legibility tier |
| `periphery_cluster_detail` | Deep dive into a specific cluster |
| `periphery_entities` | List resolved entities (people, orgs, locations…) |
| `periphery_entity_detail` | Full entity profile: aliases, mentions, relationships |
| `periphery_relationships` | Entity relationship network |
| `periphery_emerging` | Newly forming / fast-growing clusters |
| `periphery_anomalies` | Outlier documents that don't fit any cluster |
| `periphery_trajectories` | Cluster evolution over time |
| `periphery_critic_scores` | Continuous Critic coherence scores |
| `periphery_legibility_gradient` | Solid → Defined → Emerging → Haze → Whisper summary |
| `periphery_ingest` | Ingest a new document |
| `periphery_ingest_stats` | Pipeline stats: document count, queue depth |
| `periphery_pipeline_status` | Multi-space index status |
| `periphery_query_history` | Recent analytical query history |

## Setup

### Requirements

```
pip install mcp httpx
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERIPHERY_API_URL` | `http://127.0.0.1:8000` | Periphery backend URL |
| `PERIPHERY_API_TOKEN` | *(empty)* | Bearer token (if auth enabled) |
| `PERIPHERY_ADMIN_KEY` | *(empty)* | X-Admin-Key for admin endpoints |
| `PERIPHERY_API_TIMEOUT` | `30` | Request timeout in seconds |
| `PERIPHERY_MCP_PORT` | `8001` | HTTP transport port |

## Running

### stdio (for Claude Desktop / Cursor)

```bash
# From the Periphery repo root:
PYTHONPATH=. python -m periphery_mcp.server
```

### HTTP/SSE (for remote clients)

```bash
PYTHONPATH=. python -m periphery_mcp.server --http
# Serves on http://0.0.0.0:8001/sse
```

## Claude Desktop configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "periphery": {
      "command": "python",
      "args": ["-m", "periphery_mcp.server"],
      "cwd": "/path/to/Periphery",
      "env": {
        "PYTHONPATH": ".",
        "PERIPHERY_API_URL": "http://127.0.0.1:8000",
        "PERIPHERY_API_TOKEN": "your-bearer-token-if-auth-enabled"
      }
    }
  }
}
```

## Smoke test

```bash
cd /path/to/Periphery
PYTHONPATH=. python periphery_mcp/test_tools.py
```
