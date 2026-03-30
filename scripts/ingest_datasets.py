#!/usr/bin/env python3
"""Standalone bulk ingest script for ICIJ Offshore Leaks and OFAC Sanctions datasets.

Usage:
    python scripts/ingest_datasets.py --icij           # ICIJ only
    python scripts/ingest_datasets.py --ofac           # OFAC only
    python scripts/ingest_datasets.py --icij --ofac    # both

Options:
    --icij                    Ingest ICIJ Offshore Leaks
    --ofac                    Ingest OFAC SDN + Consolidated lists
    --db PATH                 SQLite DB path (default: ./data/analytical.db)
    --data-dir PATH           Directory for downloaded files (default: /app/data)
    --no-consolidated         Skip OFAC Consolidated Non-SDN list
    --node-types LIST         Comma-separated ICIJ node types (default: entities,officers,intermediaries)
    --dry-run                 Parse and count documents without writing to DB
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Ensure the project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
import structlog

from periphery.ingest.sources.icij_offshore import ICIJOffshoreSource
from periphery.ingest.sources.ofac_sanctions import OFACSanctionsSource
from periphery.rss_ingest.document_store import DocumentStore

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = structlog.get_logger(__name__)


async def ingest_icij(
    session: aiohttp.ClientSession,
    store: DocumentStore | None,
    *,
    node_types: list[str],
    data_dir: str,
    dry_run: bool,
) -> int:
    """Download and ingest ICIJ Offshore Leaks dataset."""
    source = ICIJOffshoreSource(
        enabled=True,
        node_types=node_types,
        data_dir=data_dir,
    )

    logger.info("icij_ingest_start", node_types=node_types, data_dir=data_dir)
    t0 = time.monotonic()

    docs = await source.fetch(session)

    elapsed = time.monotonic() - t0
    logger.info(
        "icij_fetch_done",
        count=len(docs),
        elapsed_s=f"{elapsed:.1f}",
    )

    if not dry_run and store is not None:
        inserted = 0
        for i, doc in enumerate(docs, 1):
            ok = await store.insert(doc)
            if ok:
                inserted += 1
            if i % 10_000 == 0:
                logger.info(
                    "icij_progress",
                    processed=i,
                    inserted=inserted,
                    total=len(docs),
                )
        logger.info("icij_ingest_done", inserted=inserted, total=len(docs))
        return inserted
    else:
        logger.info("icij_dry_run", would_insert=len(docs))
        return len(docs)


async def ingest_ofac(
    session: aiohttp.ClientSession,
    store: DocumentStore | None,
    *,
    include_consolidated: bool,
    dry_run: bool,
) -> int:
    """Download and ingest OFAC SDN and Consolidated sanctions lists."""
    source = OFACSanctionsSource(
        enabled=True,
        include_consolidated=include_consolidated,
    )

    logger.info(
        "ofac_ingest_start",
        include_consolidated=include_consolidated,
    )
    t0 = time.monotonic()

    docs = await source.fetch(session)

    elapsed = time.monotonic() - t0
    logger.info(
        "ofac_fetch_done",
        count=len(docs),
        elapsed_s=f"{elapsed:.1f}",
    )

    if not dry_run and store is not None:
        inserted = 0
        for i, doc in enumerate(docs, 1):
            ok = await store.insert(doc)
            if ok:
                inserted += 1
            if i % 1_000 == 0:
                logger.info(
                    "ofac_progress",
                    processed=i,
                    inserted=inserted,
                    total=len(docs),
                )
        logger.info("ofac_ingest_done", inserted=inserted, total=len(docs))
        return inserted
    else:
        logger.info("ofac_dry_run", would_insert=len(docs))
        return len(docs)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk ingest ICIJ Offshore Leaks and/or OFAC Sanctions data."
    )
    parser.add_argument("--icij", action="store_true", help="Ingest ICIJ Offshore Leaks")
    parser.add_argument("--ofac", action="store_true", help="Ingest OFAC Sanctions lists")
    parser.add_argument(
        "--db",
        default="./data/analytical.db",
        help="Path to SQLite document store",
    )
    parser.add_argument(
        "--data-dir",
        default="/app/data",
        help="Directory to download large files (ICIJ ZIP)",
    )
    parser.add_argument(
        "--no-consolidated",
        action="store_true",
        help="Skip OFAC Consolidated Non-SDN list",
    )
    parser.add_argument(
        "--node-types",
        default="entities,officers,intermediaries",
        help="Comma-separated ICIJ node types to ingest",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count documents without writing to DB",
    )
    args = parser.parse_args()

    if not args.icij and not args.ofac:
        parser.error("Specify at least one of --icij or --ofac")

    node_types = [t.strip() for t in args.node_types.split(",") if t.strip()]

    # Initialize document store
    store: DocumentStore | None = None
    if not args.dry_run:
        store = DocumentStore(db_path=args.db)
        await store.initialize()
        logger.info("document_store_ready", db=args.db)

    total_inserted = 0
    wall_t0 = time.monotonic()

    timeout = aiohttp.ClientTimeout(total=7200, connect=30)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "Periphery/1.0 DatasetIngestor"},
    ) as session:
        if args.icij:
            n = await ingest_icij(
                session,
                store,
                node_types=node_types,
                data_dir=args.data_dir,
                dry_run=args.dry_run,
            )
            total_inserted += n

        if args.ofac:
            n = await ingest_ofac(
                session,
                store,
                include_consolidated=not args.no_consolidated,
                dry_run=args.dry_run,
            )
            total_inserted += n

    if store is not None:
        await store.close()

    wall_elapsed = time.monotonic() - wall_t0
    logger.info(
        "ingest_complete",
        total_documents=total_inserted,
        elapsed_s=f"{wall_elapsed:.1f}",
        dry_run=args.dry_run,
    )
    print(
        f"\n✓ Done: {total_inserted:,} documents in {wall_elapsed:.1f}s"
        + (" (dry run)" if args.dry_run else "")
    )


if __name__ == "__main__":
    asyncio.run(main())
