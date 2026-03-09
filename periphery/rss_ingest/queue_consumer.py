"""Queue consumer — bridges the in-memory async queue to durable storage.

Runs as a background task alongside the polling loop. Reads documents from
the in-memory queue and ensures they are persisted to SQLite and queued for
enrichment.

The enrichment handoff is abstracted behind a simple interface so it can
swap from SQLite's ``pending_enrichment`` table to Redis Streams or a
message broker later.
"""

from __future__ import annotations

import abc
import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from .document_store import DocumentStore
    from .queue import OutputQueue

logger = structlog.get_logger(__name__)

_DEFAULT_BACKPRESSURE_THRESHOLD = 1000


class EnrichmentNotifier(abc.ABC):
    """Abstract interface for notifying downstream that a document is ready."""

    @abc.abstractmethod
    async def notify(self, document_id: str) -> None: ...


class SQLiteEnrichmentNotifier(EnrichmentNotifier):
    """Writes document IDs to the pending_enrichment table in SQLite."""

    def __init__(self, document_store: DocumentStore) -> None:
        self._store = document_store

    async def notify(self, document_id: str) -> None:
        await self._store.enqueue_for_enrichment(document_id)


class QueueConsumer:
    """Background consumer that drains the output queue into durable storage."""

    def __init__(
        self,
        queue: OutputQueue,
        document_store: DocumentStore,
        enrichment_notifier: EnrichmentNotifier | None = None,
        *,
        backpressure_threshold: int = _DEFAULT_BACKPRESSURE_THRESHOLD,
    ) -> None:
        self._queue = queue
        self._store = document_store
        self._notifier = enrichment_notifier
        self._backpressure_threshold = backpressure_threshold
        self._task: asyncio.Task | None = None
        self._running = False
        # Optional callback invoked with doc_id after successful persistence.
        # Used to fast-path notify the enrichment consumer.
        self._on_persist: Any = None
        # metrics
        self._consumed = 0
        self._persisted = 0
        self._errors = 0
        self._backpressure_warnings = 0

    @property
    def consumed(self) -> int:
        return self._consumed

    @property
    def persisted(self) -> int:
        return self._persisted

    async def start(self) -> None:
        """Start the consumer loop as a background task."""
        self._running = True
        self._task = asyncio.create_task(
            self._consume_loop(),
            name="queue_consumer",
        )
        logger.info("queue_consumer_started")

    async def stop(self) -> None:
        """Gracefully stop the consumer."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(
            "queue_consumer_stopped",
            consumed=self._consumed,
            persisted=self._persisted,
            errors=self._errors,
        )

    async def _consume_loop(self) -> None:
        """Main consumer loop — reads from queue and writes to store."""
        while self._running:
            # backpressure check
            depth = self._queue.depth()
            if depth > self._backpressure_threshold:
                self._backpressure_warnings += 1
                logger.warning(
                    "queue_backpressure",
                    depth=depth,
                    threshold=self._backpressure_threshold,
                    warning_count=self._backpressure_warnings,
                )

            try:
                doc = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            self._consumed += 1

            try:
                inserted = await self._store.insert(doc)
                if inserted:
                    self._persisted += 1
                    if self._notifier:
                        await self._notifier.notify(doc.id)
                    if self._on_persist is not None:
                        try:
                            self._on_persist(doc.id)
                        except Exception:
                            pass  # notification is optimization, not required
                    logger.debug(
                        "consumer_persisted",
                        doc_id=doc.id,
                        quality=doc.content_quality,
                    )
                else:
                    logger.debug("consumer_duplicate", doc_id=doc.id)
            except Exception as exc:
                self._errors += 1
                logger.error(
                    "consumer_persist_error",
                    doc_id=doc.id,
                    error=str(exc),
                )
