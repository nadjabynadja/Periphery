"""CLI for NC Secretary of State Business Registration data ingest.

Usage:
    python -m periphery.commands.sos_ingest --seed-file data/sos_seed_ids.txt --db ./data/periphery_documents.db
    python -m periphery.commands.sos_ingest --seed-file data/sos_seed_ids.txt --db ./data/periphery_documents.db --limit 100

Fetches NC SoS business profiles for known SOS IDs and inserts
IngestedDocument rows into SQLite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NC Secretary of State Business Registration ingest CLI"
    )
    parser.add_argument(
        "--seed-file",
        required=True,
        help="Path to seed SOS IDs file (one ID per line)",
    )
    parser.add_argument(
        "--db",
        default="./data/periphery_documents.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max number of profiles to fetch (default: 500)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Delay between requests in seconds (default: 5.0)",
    )
    return parser.parse_args()


_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status, priority, data_classification)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

NCSSOS_PROFILE_URL = "https://www.sosnc.gov/online_services/search/Business_Registration_profile?Id={sos_id}"


async def main() -> None:
    args = _get_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        print(f"Error: Seed file not found at {seed_path}")
        return

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from periphery.ingest.sources.nc_sos_business import (
        parse_sos_profile,
        _build_business_content,
    )
    from periphery.ingest.sources.base import make_document_id

    # Load seed IDs
    seed_ids = []
    with open(seed_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                seed_ids.append(line)

    if not seed_ids:
        print("No SOS IDs found in seed file.")
        return

    # Cap to limit
    batch_ids = seed_ids[: args.limit]
    print(f"Fetching {len(batch_ids)} SoS profiles (of {len(seed_ids)} total)...")

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

    t0 = time.time()
    total = 0
    errors = 0

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for sos_id in batch_ids:
            url = NCSSOS_PROFILE_URL.format(sos_id=sos_id)
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    html = await resp.text()

                fields = parse_sos_profile(html, sos_id)
                if fields is None:
                    print(f"  Skip {sos_id}: could not parse profile")
                    errors += 1
                else:
                    content = _build_business_content(fields)
                    doc_id = make_document_id("nc_sos_business", sos_id)

                    metadata = dict(fields)
                    metadata["source_type"] = "nc_sos_business"

                    now_str = datetime.now(timezone.utc).isoformat()
                    await db.execute(
                        _INSERT_DOC,
                        (
                            doc_id,
                            "NC Secretary of State",
                            "business_registration",
                            1,
                            f"{fields['entity_name']} — {fields['entity_type']} — {fields['status']}",
                            url,
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
                        ),
                    )
                    await db.commit()
                    total += 1

                    if total % 10 == 0:
                        elapsed = time.time() - t0
                        print(f"  Processed: {total} ({elapsed:.1f}s)")

            except Exception as exc:
                print(f"  Error fetching {sos_id}: {exc}")
                errors += 1

            # Rate limit
            await asyncio.sleep(args.delay)

    await db.close()
    elapsed = time.time() - t0
    print(f"\nDone! Inserted {total} SoS business profiles in {elapsed:.1f}s")
    print(f"  Errors: {errors}")
    print(f"  Database: {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
