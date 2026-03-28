"""CLI for NC voter data ingest.

Usage:
    python -m periphery.commands.voter_ingest --state NC --download --db ./data/periphery_documents.db
    python -m periphery.commands.voter_ingest --state NC --download --limit 1000 --db ./data/periphery_documents.db

Downloads NC voter registration and voter history ZIPs, parses records,
and inserts IngestedDocument rows directly into the SQLite database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp
import aiosqlite


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NC Voter Registration data ingest CLI"
    )
    parser.add_argument(
        "--state",
        default="NC",
        help="State code (currently only NC supported)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download fresh ZIP files before processing",
    )
    parser.add_argument(
        "--data-dir",
        default="./data/voter",
        help="Directory for downloaded/extracted data files",
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
        default=10000,
        help="Batch size for database inserts",
    )
    return parser.parse_args()


_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status, priority)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def _download_file(
    session: aiohttp.ClientSession, url: str, dest: Path
) -> None:
    """Stream-download a file to disk."""
    print(f"  Downloading {url} ...")
    timeout = aiohttp.ClientTimeout(total=7200, connect=30)
    async with session.get(url, timeout=timeout) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as fh:
            async for chunk in resp.content.iter_chunked(1024 * 1024):
                fh.write(chunk)
                downloaded += len(chunk)
                if total and downloaded % (100 * 1024 * 1024) < 1024 * 1024:
                    pct = downloaded * 100 // total
                    print(f"    {pct}% ({downloaded // (1024*1024)} MB)")
    print(f"  Done: {dest} ({dest.stat().st_size // (1024*1024)} MB)")


async def main() -> None:
    args = _get_args()

    if args.state != "NC":
        print(f"Error: Only NC is currently supported, got '{args.state}'")
        sys.exit(1)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    voter_zip = data_dir / "ncvoter_Statewide.zip"
    history_zip = data_dir / "ncvhis_Statewide.zip"

    # Download if requested
    if args.download:
        from periphery.ingest.sources.nc_voter import NCVOTER_ZIP_URL, NCVHIS_ZIP_URL

        async with aiohttp.ClientSession() as session:
            await _download_file(session, NCVHIS_ZIP_URL, history_zip)
            await _download_file(session, NCVOTER_ZIP_URL, voter_zip)

    if not voter_zip.exists():
        print(f"Error: Voter ZIP not found at {voter_zip}")
        print("  Use --download to fetch data first.")
        sys.exit(1)

    # Import source module for parsing
    from periphery.ingest.sources.nc_voter import (
        NCVoterSource,
        _build_history_record,
        _build_voter_content,
        _parse_row,
        NCVOTER_COLUMNS,
        NCVHIS_COLUMNS,
        _SENSITIVE_FIELDS,
    )
    from periphery.ingest.sources.base import make_document_id
    from periphery.rss_ingest.models import IngestedDocument
    from datetime import datetime, timezone
    import io
    import zipfile

    # Build history map
    print("Building voter history map...")
    t0 = time.time()
    history_map: dict[str, list[dict]] = {}

    if history_zip.exists():
        with zipfile.ZipFile(history_zip, "r") as zf:
            for name in zf.namelist():
                if "ncvhis" in name.lower():
                    with zf.open(name) as raw:
                        reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                        reader.readline()  # skip header
                        count = 0
                        for line in reader:
                            row = _parse_row(line, NCVHIS_COLUMNS)
                            if row is None:
                                continue
                            ncid = row.get("ncid", "").strip()
                            if ncid:
                                if ncid not in history_map:
                                    history_map[ncid] = []
                                history_map[ncid].append(_build_history_record(row))
                                count += 1
                                if count % 1_000_000 == 0:
                                    print(f"  History records: {count:,}")
                    break

    elapsed = time.time() - t0
    print(f"  Loaded {len(history_map):,} voter histories in {elapsed:.1f}s")

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
            priority INTEGER DEFAULT 3
        )
    """)
    await db.commit()

    # Process voter registration
    print("Processing voter registration records...")
    t0 = time.time()
    total = 0
    inserted = 0
    batch_params = []

    with zipfile.ZipFile(voter_zip, "r") as zf:
        voter_file = None
        for name in zf.namelist():
            if "ncvoter" in name.lower():
                voter_file = name
                break
        if voter_file is None:
            print("Error: No ncvoter file found in ZIP")
            await db.close()
            sys.exit(1)

        with zf.open(voter_file) as raw:
            reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            reader.readline()  # skip header

            for line in reader:
                row = _parse_row(line, NCVOTER_COLUMNS)
                if row is None:
                    continue

                ncid = row.get("ncid", "").strip()
                if not ncid:
                    continue

                total += 1
                if args.limit and total > args.limit:
                    break

                voting_history = history_map.get(ncid, [])
                first_name = row.get("first_name", "").strip()
                last_name = row.get("last_name", "").strip()
                party_cd = row.get("party_cd", "").strip()
                county_desc = row.get("county_desc", "").strip()

                content = _build_voter_content(row, len(voting_history))
                doc_id = make_document_id("nc_voter", ncid)

                metadata = {}
                for col, val in row.items():
                    if col not in _SENSITIVE_FIELDS:
                        metadata[col] = val.strip() if val else ""
                metadata["source_type"] = "nc_voter"
                metadata["voting_history"] = voting_history

                now_str = datetime.now(timezone.utc).isoformat()
                batch_params.append((
                    doc_id,
                    "NC Voter Registration",
                    "voter_registration",
                    1,
                    f"{first_name} {last_name} — {party_cd} — {county_desc}",
                    f"https://vt.ncsbe.gov/RegLkup/VoterDetail/?NCID={ncid}",
                    None,  # published
                    now_str,
                    content,
                    "",  # raw_html
                    "",  # summary
                    "full",
                    json.dumps(metadata),
                    "pending",
                    2,  # priority
                ))

                if len(batch_params) >= args.batch_size:
                    await db.executemany(_INSERT_DOC, batch_params)
                    await db.commit()
                    inserted += len(batch_params)
                    batch_params = []
                    if inserted % 100_000 == 0:
                        elapsed = time.time() - t0
                        rate = inserted / elapsed if elapsed > 0 else 0
                        print(f"  Processed: {inserted:,} ({rate:.0f}/s)")

    if batch_params:
        await db.executemany(_INSERT_DOC, batch_params)
        await db.commit()
        inserted += len(batch_params)

    elapsed = time.time() - t0
    await db.close()

    print(f"\nDone! Inserted {inserted:,} voter records in {elapsed:.1f}s")
    print(f"  Database: {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
