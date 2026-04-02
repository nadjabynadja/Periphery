"""Deep search — algorithmic web search on a person using public records data."""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")


async def search_brave(query: str, count: int = 5) -> list[str]:
    """Search using Brave Search API."""
    if not BRAVE_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": BRAVE_API_KEY},
                params={"q": query, "count": count},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = []
            for r in data.get("web", {}).get("results", []):
                title = r.get("title", "")
                url = r.get("url", "")
                desc = r.get("description", "")
                results.append(f"{title} — {desc[:200]} [{url}]")
            return results
    except Exception as e:
        logger.error(f"Brave search failed: {e}")
        return []


async def search_exa(query: str, count: int = 5) -> list[str]:
    """Search using Exa API."""
    if not EXA_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": EXA_API_KEY},
                json={"query": query, "numResults": count, "type": "neural"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = []
            for r in data.get("results", []):
                title = r.get("title", "")
                url = r.get("url", "")
                results.append(f"{title} [{url}]")
            return results
    except Exception as e:
        logger.error(f"Exa search failed: {e}")
        return []


async def run_deep_search(person: str, address: str) -> list[str]:
    """Run deep search on a person using multiple search engines.
    
    Constructs intelligent queries combining the person's name
    with their address and other public record data.
    """
    results = []

    # Build search queries
    queries = [
        f'"{person}" "{address.split(",")[0]}"',  # Name + street
        f'"{person}" public records',
        f'"{person}" campaign contributions',
        f'"{person}" business registration',
        f'"{person}" court records',
    ]

    # Run searches in parallel-ish
    for query in queries[:3]:  # Limit to 3 queries to stay within rate limits
        brave_results = await search_brave(query, count=3)
        exa_results = await search_exa(query, count=2)
        results.extend(brave_results)
        results.extend(exa_results)

    # Dedup by URL
    seen_urls = set()
    deduped = []
    for r in results:
        # Extract URL from result string
        url_start = r.rfind("[")
        url = r[url_start:] if url_start > 0 else r
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append(r)

    return deduped[:20]  # Cap at 20 results
