"""Quick smoke-test: hit each read-only tool against a live backend."""

from __future__ import annotations

import asyncio
import json
import sys

from periphery_mcp.client import PeripheryClient
from periphery_mcp.server import _dispatch, _get_client


READ_ONLY_TOOLS = [
    ("periphery_health", {}),
    ("periphery_snapshot", {}),
    ("periphery_clusters", {}),
    ("periphery_entities", {"limit": 5}),
    ("periphery_relationships", {"limit": 5}),
    ("periphery_emerging", {}),
    ("periphery_anomalies", {}),
    ("periphery_trajectories", {}),
    ("periphery_critic_scores", {}),
    ("periphery_legibility_gradient", {}),
    ("periphery_ingest_stats", {}),
    ("periphery_query_history", {"limit": 3}),
    ("periphery_search", {"query": "financial", "limit": 3}),
]


async def run() -> None:
    client = _get_client()
    passed = 0
    failed = 0

    for tool_name, args in READ_ONLY_TOOLS:
        try:
            result = await _dispatch(client, tool_name, args)
            if result.isError:
                print(f"  FAIL  {tool_name}: {result.content[0].text[:120]}")
                failed += 1
            else:
                preview = result.content[0].text[:80].replace("\n", " ")
                print(f"  OK    {tool_name}: {preview}…")
                passed += 1
        except Exception as exc:
            print(f"  ERR   {tool_name}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
