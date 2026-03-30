"""Standalone source runner — runs a single DataSource as its own process.

Usage:
    python -m periphery.ingest.sources.runner --source gdelt_doc
    python -m periphery.ingest.sources.runner --source ofac_sanctions
    python -m periphery.ingest.sources.runner --source icij_offshore
    python -m periphery.ingest.sources.runner --source rss

The runner initializes the database, builds the requested source,
and runs it in an isolated poll loop.  Each source gets its own process
so they can't block each other.

Priority is encoded as a metadata field on each ingested document so the
downstream enrichment pipeline can process higher-priority sources first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
logger = structlog.get_logger(__name__)

# Source name → priority (1 = highest)
SOURCE_PRIORITY: dict[str, int] = {
    "gdelt_doc": 1,
    "rss": 2,
    "ofac_sanctions": 3,
    "icij_offshore": 4,
}


def _get_db_path_for_source(source_name: str, settings) -> str:
    """Return the domain-specific collection DB path for a source."""
    if source_name == "rss":
        return settings.db_rss_path
    elif source_name == "gdelt_doc":
        return settings.db_gdelt_path
    elif source_name in ("ofac_sanctions", "icij_offshore"):
        return settings.db_sanctions_path
    else:
        return settings.db_analytical_path


async def run_source(source_name: str, duration: float | None = None) -> None:
    """Run a single ingestion source as a standalone process."""
    from periphery.config import get_settings
    from periphery.db import ensure_collection_database
    from periphery.rss_ingest.document_store import DocumentStore

    settings = get_settings()
    db_path = _get_db_path_for_source(source_name, settings)

    # Ensure collection DB schema exists
    await ensure_collection_database(db_path)

    priority = SOURCE_PRIORITY.get(source_name, 99)

    if source_name == "rss":
        # RSS has its own daemon with full feed management
        await _run_rss(settings, db_path, priority, duration)
        return

    # Build just the requested source
    source = _build_source(source_name, settings)
    if source is None:
        logger.error("unknown_source", source=source_name)
        sys.exit(1)

    # Force enable — we're running it explicitly
    source.enabled = True

    # Wrap with priority-tagging document store
    doc_store = DocumentStore(db_path)
    await doc_store.initialize()

    from .daemon import SourcesDaemon

    daemon = SourcesDaemon([source], document_store=doc_store)

    # Monkey-patch the document handler to inject priority metadata
    original_handler = daemon._handle_documents

    async def _priority_handler(docs):
        for doc in docs:
            if doc.metadata is None:
                doc.metadata = {}
            doc.metadata["ingest_priority"] = priority
            doc.metadata["ingest_source_process"] = source_name
        await original_handler(docs)

    daemon._handle_documents = _priority_handler

    # Signal handling
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("shutdown_signal", source=source_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await daemon.start()
    logger.info(
        "source_runner_started",
        source=source_name,
        priority=priority,
        poll_interval=source.poll_interval,
    )

    if duration is not None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            logger.info("duration_elapsed", source=source_name, duration=duration)
    else:
        await stop_event.wait()

    await daemon.stop()
    logger.info("source_runner_stopped", source=source_name)


async def _run_rss(settings, db_path: str, priority: int, duration: float | None) -> None:
    """Run the RSS daemon with priority tagging."""
    from periphery.rss_ingest.daemon import RSSIngestDaemon

    daemon = RSSIngestDaemon(
        config_path=settings.rss_feeds_config or None,
        fetch_full_articles=settings.rss_fetch_full_articles,
        db_path=db_path,
    )

    # Patch the document store's insert to tag priority
    original_insert = daemon.document_store.insert

    async def _priority_insert(doc):
        if doc.metadata is None:
            doc.metadata = {}
        doc.metadata["ingest_priority"] = priority
        doc.metadata["ingest_source_process"] = "rss"
        return await original_insert(doc)

    daemon.document_store.insert = _priority_insert

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("shutdown_signal", source="rss")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await daemon.start()
    logger.info("rss_runner_started", priority=priority)

    if duration is not None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            logger.info("duration_elapsed", source="rss", duration=duration)
    else:
        await stop_event.wait()

    await daemon.stop()
    logger.info("rss_runner_stopped")


def _build_source(source_name: str, settings):
    """Build a single DataSource by name."""
    from .gdelt_doc import GDELTDocSource
    from .icij_offshore import ICIJOffshoreSource
    from .ofac_sanctions import OFACSanctionsSource

    builders = {
        "gdelt_doc": lambda: GDELTDocSource(
            poll_interval=settings.gdelt_poll_interval,
            enabled=True,
            max_articles_per_query=settings.gdelt_max_articles_per_query,
        ),
        "ofac_sanctions": lambda: OFACSanctionsSource(
            poll_interval=settings.ofac_poll_interval,
            enabled=True,
            include_consolidated=settings.ofac_include_consolidated,
        ),
        "icij_offshore": lambda: ICIJOffshoreSource(
            poll_interval=settings.icij_poll_interval,
            enabled=True,
            node_types=[s.strip() for s in settings.icij_node_types.split(",") if s.strip()],
            data_dir=settings.icij_data_dir,
        ),
    }

    builder = builders.get(source_name)
    return builder() if builder else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single ingestion source")
    parser.add_argument(
        "--source",
        required=True,
        choices=list(SOURCE_PRIORITY.keys()),
        help="Source to run",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Exit after N seconds (useful for testing)",
    )
    args = parser.parse_args()
    asyncio.run(run_source(args.source, duration=args.duration))


if __name__ == "__main__":
    main()
