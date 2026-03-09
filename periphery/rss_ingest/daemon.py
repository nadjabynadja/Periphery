"""RSS Ingest Daemon — the main orchestrator.

Wires together FeedManager, PollingEngine, Deduplicator, OutputQueue,
and the FastAPI status endpoint into a single long-running daemon.

Can be run standalone::

    python -m periphery.rss_ingest.daemon

Or integrated into the main Periphery FastAPI app by mounting its router.
"""

from __future__ import annotations

import asyncio
import signal
import time
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from .dedup import Deduplicator
from .feed_manager import FeedManager
from .poller import PollingEngine
from .queue import InProcessQueue, OutputQueue
from .status import register_daemon, router as status_router

logger = structlog.get_logger(__name__)


class RSSIngestDaemon:
    """Top-level daemon that runs the RSS ingest pipeline."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        fetch_full_articles: bool = True,
        queue_maxsize: int = 10_000,
    ) -> None:
        self.feed_manager = FeedManager(config_path)
        self.deduplicator = Deduplicator()
        self.output_queue: OutputQueue = InProcessQueue(maxsize=queue_maxsize)
        self.poller = PollingEngine(
            self.feed_manager,
            self.deduplicator,
            self.output_queue,
            fetch_full_articles=fetch_full_articles,
        )
        self._start_time: float = 0.0

    @property
    def uptime(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    async def start(self) -> None:
        """Start the polling engine."""
        self._start_time = time.time()
        register_daemon(self)
        await self.poller.start()
        logger.info(
            "rss_daemon_started",
            feeds=len(self.feed_manager.feeds),
            categories=self.feed_manager.categories,
        )

    async def stop(self) -> None:
        """Gracefully shut down."""
        await self.poller.stop()
        logger.info("rss_daemon_stopped", uptime=self.uptime)


def create_app(config_path: str | Path | None = None) -> FastAPI:
    """Create a standalone FastAPI app for the RSS daemon."""
    app = FastAPI(title="Periphery RSS Ingest", version="0.1.0")
    app.include_router(status_router)
    daemon = RSSIngestDaemon(config_path)

    @app.on_event("startup")
    async def _startup() -> None:
        await daemon.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await daemon.stop()

    return app


async def run_daemon(config_path: str | Path | None = None) -> None:
    """Run the daemon as a standalone async process (no HTTP server)."""
    daemon = RSSIngestDaemon(config_path)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await daemon.start()
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
        asyncio.run(run_daemon(args.config))
    else:
        app = create_app(args.config)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
