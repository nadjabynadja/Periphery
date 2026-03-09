"""Output queue — async in-process queue behind a swappable interface.

Start simple with ``asyncio.Queue``.  The abstract base class lets us
swap to Redis Streams or RabbitMQ later without touching the daemon.
"""

from __future__ import annotations

import abc
import asyncio

import structlog

from .models import IngestedDocument

logger = structlog.get_logger(__name__)


class OutputQueue(abc.ABC):
    """Abstract output queue for ingested documents."""

    @abc.abstractmethod
    async def put(self, doc: IngestedDocument) -> None: ...

    @abc.abstractmethod
    async def get(self) -> IngestedDocument: ...

    @abc.abstractmethod
    def depth(self) -> int: ...


class InProcessQueue(OutputQueue):
    """Simple asyncio.Queue-backed output queue."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._q: asyncio.Queue[IngestedDocument] = asyncio.Queue(maxsize=maxsize)

    async def put(self, doc: IngestedDocument) -> None:
        await self._q.put(doc)
        logger.debug("queue_put", doc_id=doc.id, depth=self._q.qsize())

    async def get(self) -> IngestedDocument:
        return await self._q.get()

    def depth(self) -> int:
        return self._q.qsize()
