"""Exa search client for external intelligence retrieval.

Wraps the Exa Python SDK to provide async search with TTL caching,
graceful error handling, and structured results compatible with
the Periphery query pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ExaSource(BaseModel):
    title: str
    url: str
    published_date: str | None = None
    text: str  # excerpt
    score: float = 0.0
    author: str | None = None


class ExaSearchResult(BaseModel):
    sources: list[ExaSource] = Field(default_factory=list)
    query_used: str = ""  # the actual query sent to Exa (may differ from user query)
    search_time_ms: int = 0
    enabled: bool = True


# TTL cache entry
class _CacheEntry:
    __slots__ = ("result", "expires_at")

    def __init__(self, result: ExaSearchResult, ttl_seconds: float) -> None:
        self.result = result
        self.expires_at = time.monotonic() + ttl_seconds


class ExaSearchClient:
    """Async wrapper around Exa's synchronous SDK."""

    def __init__(
        self,
        api_key: str,
        max_results: int = 10,
        enabled: bool = True,
        cache_ttl_seconds: float = 300.0,  # 5 minutes
    ) -> None:
        from exa_py import Exa

        self._client = Exa(api_key=api_key)
        self._max_results = max_results
        self._enabled = enabled
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}

    async def search(
        self,
        query: str,
        intent_context: dict[str, Any] | None = None,
    ) -> ExaSearchResult:
        """Search Exa for external intelligence relevant to *query*.

        Never raises — returns an empty result on any failure.
        """
        if not self._enabled:
            return ExaSearchResult(enabled=False)

        # Build the effective query
        effective_query = self._build_query(query, intent_context)

        # Check cache
        cached = self._get_cached(effective_query)
        if cached is not None:
            return cached

        start = time.monotonic()
        try:
            search_kwargs = self._build_search_kwargs(effective_query, intent_context)
            # Exa SDK is synchronous — run in a thread to avoid blocking
            response = await asyncio.to_thread(
                self._client.search_and_contents, effective_query, **search_kwargs
            )
            result = self._parse_response(response, effective_query, start)
        except Exception:
            logger.warning("exa_search_failed", exc_info=True)
            result = ExaSearchResult(query_used=effective_query)

        # Store in cache
        self._cache[effective_query] = _CacheEntry(result, self._cache_ttl)
        self._evict_expired()

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_query(
        self, query: str, intent_context: dict[str, Any] | None
    ) -> str:
        """Enrich the raw query with entity names from parsed intent."""
        if not intent_context:
            return query

        entity_names: list[str] = intent_context.get("entity_names", [])
        if entity_names:
            # Append entity names that aren't already in the query
            extras = [n for n in entity_names if n.lower() not in query.lower()]
            if extras:
                return f"{query} {' '.join(extras)}"
        return query

    def _build_search_kwargs(
        self,
        query: str,
        intent_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build keyword arguments for ``search_and_contents``."""
        kwargs: dict[str, Any] = {
            "num_results": self._max_results,
            "type": "auto",
            "text": {"max_characters": 1500},
        }

        if not intent_context:
            return kwargs

        temporal_focus: str | None = intent_context.get("temporal_focus")
        if temporal_focus:
            import datetime as _dt

            today = _dt.date.today()
            if "last 7 days" in temporal_focus or "past week" in temporal_focus:
                kwargs["start_published_date"] = (
                    today - _dt.timedelta(days=7)
                ).isoformat()
            elif "last 30 days" in temporal_focus or "past month" in temporal_focus:
                kwargs["start_published_date"] = (
                    today - _dt.timedelta(days=30)
                ).isoformat()
            elif "last 24 hours" in temporal_focus or "today" in temporal_focus:
                kwargs["start_published_date"] = (
                    today - _dt.timedelta(days=1)
                ).isoformat()

        return kwargs

    def _parse_response(
        self, response: Any, query_used: str, start: float
    ) -> ExaSearchResult:
        """Convert Exa SDK response to ``ExaSearchResult``."""
        sources: list[ExaSource] = []
        for result in response.results:
            sources.append(
                ExaSource(
                    title=getattr(result, "title", "") or "",
                    url=getattr(result, "url", "") or "",
                    published_date=getattr(result, "published_date", None),
                    text=getattr(result, "text", "") or "",
                    score=getattr(result, "score", 0.0) or 0.0,
                    author=getattr(result, "author", None),
                )
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ExaSearchResult(
            sources=sources,
            query_used=query_used,
            search_time_ms=elapsed_ms,
        )

    def _get_cached(self, key: str) -> ExaSearchResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._cache[key]
            return None
        return entry.result

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._cache.items() if now > v.expires_at]
        for k in expired:
            del self._cache[k]
