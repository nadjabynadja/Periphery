"""Sources Daemon — orchestrates all external data source pollers.

Manages lifecycle of all DataSource instances, pushes their output
into the standard document pipeline via the same OutputQueue and
DocumentStore used by the RSS daemon.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.document_store import DocumentStore
from periphery.rss_ingest.models import IngestedDocument
from periphery.rss_ingest.queue import OutputQueue

from .base import DataSource

logger = structlog.get_logger(__name__)


class SourcesDaemon:
    """Top-level daemon that manages all external data sources.

    Documents produced by sources are pushed to either a shared
    OutputQueue (when co-located with the RSS daemon) or directly
    persisted to the DocumentStore.
    """

    def __init__(
        self,
        sources: list[DataSource],
        *,
        output_queue: OutputQueue | None = None,
        document_store: DocumentStore | None = None,
    ) -> None:
        self._sources = sources
        self._output_queue = output_queue
        self._document_store = document_store
        self._session: aiohttp.ClientSession | None = None
        self._start_time: float = 0.0

    @property
    def sources(self) -> list[DataSource]:
        return self._sources

    @property
    def uptime(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    async def start(self) -> None:
        """Start all enabled sources."""
        self._start_time = time.time()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Periphery/1.0 SourcesDaemon"},
        )

        enabled_count = 0
        for source in self._sources:
            source._on_documents = self._handle_documents
            if source.enabled:
                await source.start(self._session)
                enabled_count += 1

        logger.info(
            "sources_daemon_started",
            total=len(self._sources),
            enabled=enabled_count,
            sources=[s.name for s in self._sources if s.enabled],
        )

    async def stop(self) -> None:
        """Stop all sources and close the shared HTTP session."""
        for source in self._sources:
            await source.stop()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("sources_daemon_stopped", uptime=self.uptime)

    async def _handle_documents(self, docs: list[IngestedDocument]) -> None:
        """Route documents from sources into the pipeline."""
        for doc in docs:
            if self._output_queue is not None:
                await self._output_queue.put(doc)
            elif self._document_store is not None:
                await self._document_store.insert(doc)

    def health(self) -> dict[str, Any]:
        """Return daemon health and per-source metrics."""
        return {
            "uptime_seconds": self.uptime,
            "total_sources": len(self._sources),
            "enabled_sources": sum(1 for s in self._sources if s.enabled),
            "sources": {s.name: s.health() for s in self._sources},
        }
