"""Priority-aware poll scheduler.

Not all feeds are equal. When there's contention for rate-limited slots,
high-value sources (priority 1) should be serviced before low-value ones
(priority 4).

Uses ``asyncio.PriorityQueue`` to order pending poll requests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from .models import FeedConfig

logger = structlog.get_logger(__name__)


@dataclass(order=True)
class PollRequest:
    """A queued request to poll a feed, ordered by priority."""

    priority: int
    feed: FeedConfig = field(compare=False)
    # monotonic counter to break ties (FIFO within same priority)
    _seq: int = field(default=0, compare=True)


class PriorityScheduler:
    """Schedules feed polls by priority.

    Workers pull from the priority queue; higher-priority feeds (lower
    number) always get serviced first.

    Usage::

        scheduler = PriorityScheduler()
        scheduler.submit(feed_config)
        feed = await scheduler.next()
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.PriorityQueue[PollRequest] = asyncio.PriorityQueue(
            maxsize=maxsize
        )
        self._seq = 0

    def submit(self, feed: FeedConfig) -> None:
        """Enqueue a feed for polling.  Non-blocking; drops if queue is full."""
        req = PollRequest(priority=feed.priority, feed=feed, _seq=self._seq)
        self._seq += 1
        try:
            self._queue.put_nowait(req)
        except asyncio.QueueFull:
            logger.warning("scheduler_queue_full", feed=feed.name, priority=feed.priority)

    async def next(self) -> FeedConfig:
        """Wait for the next highest-priority feed to poll."""
        req = await self._queue.get()
        return req.feed

    def pending(self) -> int:
        """Number of feeds waiting to be polled."""
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
