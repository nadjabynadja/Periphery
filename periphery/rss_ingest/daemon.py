"""RSS Ingest Daemon — the main orchestrator.

Wires together FeedManager, PollingEngine, Deduplicator, OutputQueue,
RateLimiterChain, RobotsChecker, DocumentStore, QueueConsumer, and the
FastAPI status endpoint into a single long-running daemon.

Can be run standalone::

    python -m periphery.rss_ingest.daemon

Or integrated into the main Periphery FastAPI app by mounting its router.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from .dedup import Deduplicator
from .document_store import DocumentStore
from .feed_manager import FeedManager
from .poller import PollingEngine
from .queue import InProcessQueue, OutputQueue
from .queue_consumer import QueueConsumer, SQLiteEnrichmentNotifier
from .rate_limiter import RateLimiterChain
from .robots_checker import RobotsChecker
from .status import register_daemon, router as status_router

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = os.environ.get(
    "PERIPHERY_DB_PATH", "./data/rss.db"
)


class RSSIngestDaemon:
    """Top-level daemon that runs the RSS ingest pipeline."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        fetch_full_articles: bool = True,
        queue_maxsize: int = 10_000,
        db_path: str | Path | None = None,
    ) -> None:
        self.feed_manager = FeedManager(config_path)
        self.document_store = DocumentStore(db_path or _DEFAULT_DB_PATH)
        self.deduplicator = Deduplicator(document_store=self.document_store)
        self.output_queue: OutputQueue = InProcessQueue(maxsize=queue_maxsize)
        self.rate_limiter = RateLimiterChain(self.feed_manager.rate_limit_config)
        self.robots_checker: RobotsChecker | None = None  # initialized on start()
        self.poller = PollingEngine(
            self.feed_manager,
            self.deduplicator,
            self.output_queue,
            self.rate_limiter,
            document_store=self.document_store,
            fetch_full_articles=fetch_full_articles,
        )
        self.queue_consumer: QueueConsumer | None = None
        self._start_time: float = 0.0

    @property
    def uptime(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    async def start(self) -> None:
        """Start the polling engine, document store, and queue consumer."""
        self._start_time = time.time()

        # initialize SQLite persistence
        await self.document_store.initialize()

        # register for status endpoints
        register_daemon(self)

        # start polling engine
        await self.poller.start()

        # attach robots checker now that the session exists
        if self.poller._session:
            self.robots_checker = RobotsChecker(self.poller._session)
            self.poller._robots_checker = self.robots_checker

        # start queue consumer
        notifier = SQLiteEnrichmentNotifier(self.document_store)
        self.queue_consumer = QueueConsumer(
            self.output_queue,
            self.document_store,
            enrichment_notifier=notifier,
        )
        await self.queue_consumer.start()

        logger.info(
            "rss_daemon_started",
            feeds=len(self.feed_manager.feeds),
            categories=self.feed_manager.categories,
            max_concurrent=self.feed_manager.rate_limit_config.max_concurrent_requests,
            max_rpm=self.feed_manager.rate_limit_config.max_requests_per_minute,
            db_path=str(self.document_store._db_path),
        )

    async def stop(self) -> None:
        """Gracefully shut down."""
        if self.queue_consumer:
            await self.queue_consumer.stop()
        await self.poller.stop()
        await self.document_store.close()
        logger.info("rss_daemon_stopped", uptime=self.uptime)


def create_app(config_path: str | Path | None = None) -> FastAPI:
    """Create a standalone FastAPI app for the RSS daemon."""
    from contextlib import asynccontextmanager

    daemon = RSSIngestDaemon(config_path)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        await daemon.start()
        yield
        await daemon.stop()

    app = FastAPI(title="Periphery RSS Ingest", version="0.1.0", lifespan=_lifespan)
    app.include_router(status_router)
    return app


async def run_daemon(
    config_path: str | Path | None = None,
    *,
    duration: float | None = None,
) -> None:
    """Run the daemon as a standalone async process (no HTTP server).

    Args:
        duration: If set, stop automatically after this many seconds.
    """
    daemon = RSSIngestDaemon(config_path)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await daemon.start()

    if duration is not None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            logger.info("run_duration_elapsed", duration=duration)
    else:
        await stop_event.wait()

    await daemon.stop()


def main() -> None:
    """Entry point — runs the FastAPI app with uvicorn."""
    import argparse

    parser = argparse.ArgumentParser(description="Periphery RSS Ingest Daemon")
    parser.add_argument("--config", type=str, default=None, help="Path to feeds.yaml")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8001, help="Bind port")
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Run without HTTP status endpoint",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (default: ./data/rss.db)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Exit after this many seconds (useful when invoked from cron).",
    )
    args = parser.parse_args()

    # configure structlog for JSON output
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )

    if args.no_server:
        asyncio.run(run_daemon(args.config, duration=args.duration))
    else:
        app = create_app(args.config)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
