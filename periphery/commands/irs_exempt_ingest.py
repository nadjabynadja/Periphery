"""CLI for IRS Exempt Organizations (NC) data ingest.

Usage:
    python -m periphery.commands.irs_exempt_ingest --download --db ./data/periphery_documents.db
    python -m periphery.commands.irs_exempt_ingest --csv ./data/eo_nc.csv --db ./data/periphery_documents.db

Downloads the IRS BMF CSV for NC exempt organizations, parses records,
and inserts IngestedDocument rows into SQLite.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IRS Exempt Organizations (NC) data ingest CLI"
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download fresh eo_nc.csv from IRS before processing",
    )
    parser.add_argument(
        "--csv",
        default="./data/eo_nc.csv",
        help="Path to eo_nc.csv file (downloaded or local)",
    )
    parser.add_argument(
        "--db",
        default="./data/periphery_documents.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of records to process (0 = all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Batch size for database inserts",
    )
    return parser.parse_args()


_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status, priority, data_classification)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

IRS_EO_NC_URL = "https://www.irs.gov/pub/irs-soi/eo_nc.csv"


async def _download_csv(session: aiohttp.ClientSession, dest: Path) -> None:
    """Download the IRS BMF CSV for NC."""
    print(f"  Downloading {IRS_EO_NC_URL} ...")
    timeout = aiohttp.ClientTimeout(total=120, connect=30)
    async with session.get(IRS_EO_NC_URL, timeout=timeout) as resp:
        resp.raise_for_status()
        data = await resp.read()
        dest.write_bytes(data)
    print(f"  Done: {dest} ({dest.stat().st_size // 1024} KB)")


async def main() -> None:
    args = _get_args()

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from periphery.ingest.sources.irs_exempt_orgs import (
        _build_org_content,
        _ntee_description,
    )
    from periphery.ingest.sources.base import make_document_id
    from urllib.parse import quote_plus

    # Download if requested
    if args.download:
        async with aiohttp.ClientSession() as session:
            await _download_csv(session, csv_path)

    if not csv_path.exists():
        print(f"Error: CSV not found at {csv_path}")
        print("  Use --download to fetch data first.")
        return

    # Open database
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")

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

    print(f"Processing IRS Exempt Orgs CSV ({csv_path})...")
    t0 = time.time()
    total = 0
    batch_params = []
    seen_eins: set[str] = set()

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ein = (row.get("EIN") or "").strip()
            name = (row.get("NAME") or "").strip()
            city = (row.get("CITY") or "").strip()
            ntee_cd = (row.get("NTEE_CD") or "").strip()

            if not ein or not name:
                continue
            if ein in seen_eins:
                continue
            seen_eins.add(ein)

            total += 1
            if args.limit and total > args.limit:
                break

            content = _build_org_content(row)
            doc_id = make_document_id("irs_exempt", ein)
            encoded_name = quote_plus(name)

            metadata = {}
            for key in row:
                val = row[key]
                metadata[key.lower()] = val.strip() if isinstance(val, str) else str(val)
            metadata["source_type"] = "irs_exempt_orgs"
            metadata["ntee_description"] = _ntee_description(ntee_cd)

            now_str = datetime.now(timezone.utc).isoformat()
            batch_params.append((
                doc_id,
                "IRS Exempt Organizations",
                "business_nonprofit",
                1,
                f"{name} — EIN {ein} — {city}, NC",
                f"https://apps.irs.gov/app/eos/detailsPage?ein={ein}&name={encoded_name}&city={quote_plus(city)}&state=NC",
                None,
                now_str,
                content,
                "",
                "",
                "full",
                json.dumps(metadata),
                "pending",
                2,
                "PUBLIC",
            ))

            if len(batch_params) >= args.batch_size:
                await db.executemany(_INSERT_DOC, batch_params)
                await db.commit()
                elapsed = time.time() - t0
                rate = total / elapsed if elapsed > 0 else 0
                print(f"  Processed: {total:,} ({rate:.0f}/s)")
                batch_params = []

    if batch_params:
        await db.executemany(_INSERT_DOC, batch_params)
        await db.commit()

    await db.close()
    elapsed = time.time() - t0
    print(f"\nDone! Inserted {total:,} IRS exempt org records in {elapsed:.1f}s")
    print(f"  Database: {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
