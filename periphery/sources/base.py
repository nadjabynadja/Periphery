"""Abstract base class for all external data sources."""

from __future__ import annotations

import abc
import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_document_id(source_name: str, unique_key: str) -> str:
    """Deterministic document ID from source + key to enable dedup."""
    raw = f"{source_name}:{unique_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class DataSource(abc.ABC):
    """Base class for polling external data sources.

    Subclasses implement ``fetch()`` to return a list of IngestedDocument
    objects. The base class handles the poll loop, rate-limiting, error
    backoff, and metrics.
    """

    name: str = "unnamed"
    category: str = "external"
    default_poll_interval: int = 300  # seconds

    def __init__(
        self,
        *,
        poll_interval: int | None = None,
        enabled: bool = True,
        max_backoff: int = 3600,
    ) -> None:
        self.poll_interval = poll_interval or self.default_poll_interval
        self.enabled = enabled
        self._max_backoff = max_backoff
        self._session: aiohttp.ClientSession | None = None
        self._owns_session = False
        self._running = False
        self._task: asyncio.Task | None = None

        # Metrics
        self.total_fetched = 0
        self.total_errors = 0
        self.consecutive_errors = 0
        self.last_fetch: datetime | None = None
        self.last_error: str | None = None

    @abc.abstractmethod
    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Poll the source and return new documents.

        Implementations should handle their own pagination and return
        only genuinely new observations since the last call.
        """
        ...

    async def start(self, session: aiohttp.ClientSession | None = None) -> None:
        """Start the polling loop."""
        if not self.enabled:
            logger.info("source_disabled", source=self.name)
            return
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Periphery/1.0"},
        )
        self._running = True
        self._task = asyncio.create_task(
            self._poll_loop(), name=f"source-{self.name}"
        )
        logger.info("source_started", source=self.name, interval=self.poll_interval)

    async def stop(self) -> None:
        """Stop the polling loop and close self-owned session."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None
        logger.info(
            "source_stopped",
            source=self.name,
            total_fetched=self.total_fetched,
            total_errors=self.total_errors,
        )

    async def _poll_loop(self) -> None:
        """Main loop: fetch → sleep → repeat, with exponential backoff on errors."""
        assert self._session is not None
        while self._running:
            try:
                docs = await self.fetch(self._session)
                self.total_fetched += len(docs)
                self.consecutive_errors = 0
                self.last_fetch = _utcnow()

                if docs:
                    await self._emit(docs)
                    logger.info(
                        "source_fetched",
                        source=self.name,
                        count=len(docs),
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.total_errors += 1
                self.consecutive_errors += 1
                self.last_error = str(exc)
                logger.error(
                    "source_fetch_error",
                    source=self.name,
                    error=str(exc),
                    consecutive=self.consecutive_errors,
                )

            # Exponential backoff on consecutive errors
            if self.consecutive_errors > 0:
                backoff = min(
                    self.poll_interval * (2 ** (self.consecutive_errors - 1)),
                    self._max_backoff,
                )
            else:
                backoff = self.poll_interval

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return

    async def _emit(self, docs: list[IngestedDocument]) -> None:
        """Push documents to the output callback. Set by SourcesDaemon."""
        if self._on_documents is not None:
            await self._on_documents(docs)

    _on_documents: Any = None

    def health(self) -> dict[str, Any]:
        """Return source health metrics."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "running": self._running,
            "poll_interval": self.poll_interval,
            "total_fetched": self.total_fetched,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
            "last_fetch": self.last_fetch.isoformat() if self.last_fetch else None,
            "last_error": self.last_error,
        }
