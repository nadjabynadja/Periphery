"""CLI for NC Property Records (Parcels) ingest.

Usage:
    python -m periphery.commands.parcels_ingest --download --db ./data/analytical.db
    python -m periphery.commands.parcels_ingest --download --db ./data/analytical.db --limit 1000
    python -m periphery.commands.parcels_ingest --download --db ./data/analytical.db --county WAKE

Queries the NC OneMap Statewide Parcels FeatureServer REST API,
pages through results, and inserts IngestedDocument rows into SQLite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NC Property Records (Parcels) ingest CLI"
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Query the FeatureServer API (required to fetch data)",
    )
    parser.add_argument(
        "--db",
        default="./data/analytical.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of records to process (0 = all)",
    )
    parser.add_argument(
        "--county",
        type=str,
        default=None,
        help="Filter by county name (e.g., WAKE, MECKLENBURG)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="Batch size for database inserts",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=2000,
        help="API page size (max typically 2000)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between API requests in seconds",
    )
    return parser.parse_args()


_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status, priority, data_classification)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def main() -> None:
    args = _get_args()

    if not args.download:
        print("Error: --download flag is required to query the FeatureServer API")
        sys.exit(1)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from periphery.ingest.sources.nc_parcels import (
        FEATURESERVER_BASE_URL,
        PARCELS_DATASET_URL,
        _build_parcel_content,
        _build_parcel_title,
    )
    from periphery.ingest.sources.base import make_document_id

    # Build WHERE clause
    if args.county:
        safe_county = args.county.replace("'", "''")
        where = f"UPPER(cntyname)='{safe_county.upper()}'"
    else:
        where = "1=1"

    # Open database
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")

    # Ensure table exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_feed TEXT NOT NULL,
            source_category TEXT,
            source_credibility_tier INTEGER,
            title TEXT,
            url TEXT,
            published TIMESTAMP,
            ingested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            content TEXT,
            raw_html TEXT,
            summary TEXT,
            content_quality TEXT DEFAULT 'full',
            metadata JSON,
            processing_status TEXT DEFAULT 'pending',
            processing_error TEXT,
            priority INTEGER DEFAULT 3,
            data_classification TEXT DEFAULT 'PUBLIC'
        )
    """)
    await db.commit()

    print(f"Querying NC OneMap Parcels FeatureServer...")
    if args.county:
        print(f"  Filtering by county: {args.county.upper()}")
    if args.limit:
        print(f"  Limiting to {args.limit:,} records")

    t0 = time.time()
    total = 0
    inserted = 0
    batch_params = []
    offset = 0

    timeout = aiohttp.ClientTimeout(total=120, connect=30)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "Periphery/1.0"},
    ) as session:
        while True:
            if args.limit and total >= args.limit:
                break

            params = {
                "where": where,
                "outFields": "*",
                "outSR": "4326",
                "resultOffset": str(offset),
                "resultRecordCount": str(args.page_size),
                "f": "json",
            }

            url = f"{FEATURESERVER_BASE_URL}/query"
            try:
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except Exception as exc:
                print(f"  API error at offset {offset}: {exc}")
                break

            features = data.get("features", [])
            if not features:
                break

            for feature in features:
                if args.limit and total >= args.limit:
                    break

                attrs = feature.get("attributes", {})
                geometry = feature.get("geometry")

                objectid = attrs.get("objectid") or attrs.get("OBJECTID")
                if objectid is None:
                    continue

                # Extract coordinates
                latitude = None
                longitude = None
                if geometry:
                    longitude = geometry.get("x")
                    latitude = geometry.get("y")

                # Build metadata
                metadata = dict(attrs)
                metadata["source_type"] = "nc_parcels"
                if latitude is not None:
                    metadata["latitude"] = latitude
                if longitude is not None:
                    metadata["longitude"] = longitude

                doc_id = make_document_id("nc_parcels", str(objectid))
                now_str = datetime.now(timezone.utc).isoformat()

                batch_params.append((
                    doc_id,
                    "NC Property Records",
                    "property_records",
                    1,
                    _build_parcel_title(attrs),
                    PARCELS_DATASET_URL,
                    None,  # published
                    now_str,
                    _build_parcel_content(attrs),
                    "",  # raw_html
                    "",  # summary
                    "full",
                    json.dumps(metadata),
                    "pending",
                    2,  # priority
                    "PII",  # data_classification
                ))
                total += 1

                if len(batch_params) >= args.batch_size:
                    await db.executemany(_INSERT_DOC, batch_params)
                    await db.commit()
                    inserted += len(batch_params)
                    batch_params = []
                    elapsed = time.time() - t0
                    rate = inserted / elapsed if elapsed > 0 else 0
                    print(f"  Processed: {inserted:,} ({rate:.0f}/s)")

            offset += len(features)

            # Check if more results
            exceeded = data.get("exceededTransferLimit", False)
            if not exceeded:
                break

            # Rate limit
            await asyncio.sleep(args.delay)

    # Final batch
    if batch_params:
        await db.executemany(_INSERT_DOC, batch_params)
        await db.commit()
        inserted += len(batch_params)

    elapsed = time.time() - t0
    await db.close()

    print(f"\nDone! Inserted {inserted:,} parcel records in {elapsed:.1f}s")
    print(f"  Database: {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
