"""CLI for FEC individual contributions data ingest.

Usage:
    python -m periphery.commands.fec_ingest --cycle 2024 --state NC --download --db ./data/periphery_documents.db
    python -m periphery.commands.fec_ingest --cycle 2024 --state NC --download --limit 1000 --db ./data/periphery_documents.db

Downloads FEC bulk contribution ZIP files, parses pipe-delimited records,
filters to the configured state, and inserts IngestedDocument rows into SQLite.
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
        description="FEC Individual Contributions data ingest CLI"
    )
    parser.add_argument(
        "--cycle",
        default="2024",
        help="Election cycle year (e.g. 2024, 2022). Comma-separated for multiple.",
    )
    parser.add_argument(
        "--state",
        default="NC",
        help="State code to filter contributions (default: NC)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download fresh ZIP files before processing",
    )
    parser.add_argument(
        "--data-dir",
        default="./data/fec",
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
     metadata, processing_status, priority, data_classification)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    cycles = [c.strip() for c in args.cycle.split(",") if c.strip()]
    state_filter = args.state.upper()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from periphery.ingest.sources.fec_contributions import (
        FEC_FIELDS,
        _build_fec_url,
        _build_contribution_content,
        _cycle_to_short,
        _parse_fec_line,
    )
    from periphery.ingest.sources.base import make_document_id
    from urllib.parse import quote_plus
    import io
    import zipfile

    # Download if requested
    if args.download:
        async with aiohttp.ClientSession() as session:
            for cycle in cycles:
                url = _build_fec_url(cycle)
                dest = data_dir / f"indiv{_cycle_to_short(cycle)}.zip"
                await _download_file(session, url, dest)

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

    # Process each cycle
    grand_total = 0

    for cycle in cycles:
        short = _cycle_to_short(cycle)
        zip_path = data_dir / f"indiv{short}.zip"

        if not zip_path.exists():
            print(f"Error: FEC ZIP not found at {zip_path}")
            print("  Use --download to fetch data first.")
            continue

        print(f"Processing FEC cycle {cycle} ({zip_path})...")
        t0 = time.time()
        total = 0
        batch_params = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find data file
            data_file = None
            for name in zf.namelist():
                if "itcont" in name.lower() and name.lower().endswith(".txt"):
                    data_file = name
                    break
            if data_file is None:
                for name in zf.namelist():
                    if name.lower().endswith(".txt"):
                        data_file = name
                        break
            if data_file is None:
                print(f"  Error: No data file found in {zip_path}")
                continue

            with zf.open(data_file) as raw:
                reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                # No header row in FEC bulk files

                for line in reader:
                    row = _parse_fec_line(line)
                    if row is None:
                        continue

                    state = row.get("STATE", "").strip().upper()
                    if state != state_filter:
                        continue

                    sub_id = row.get("SUB_ID", "").strip()
                    if not sub_id:
                        continue

                    total += 1
                    if args.limit and total > args.limit:
                        break

                    name = row.get("NAME", "").strip()
                    cmte_id = row.get("CMTE_ID", "").strip()
                    amount = row.get("TRANSACTION_AMT", "").strip()
                    date = row.get("TRANSACTION_DT", "").strip()

                    content = _build_contribution_content(row)
                    doc_id = make_document_id("fec_contributions", sub_id)
                    encoded_name = quote_plus(name)

                    metadata = {}
                    for field in FEC_FIELDS:
                        metadata[field] = row.get(field, "").strip()
                    metadata["source_type"] = "fec_contributions"
                    metadata["cycle"] = cycle

                    now_str = datetime.now(timezone.utc).isoformat()
                    batch_params.append((
                        doc_id,
                        "FEC Individual Contributions",
                        "campaign_finance",
                        1,
                        f"{name} — ${amount} to {cmte_id} ({date})",
                        f"https://www.fec.gov/data/receipts/individual-contributions/?contributor_name={encoded_name}&contributor_state={state_filter}",
                        None,  # published
                        now_str,
                        content,
                        "",  # raw_html
                        "",  # summary
                        "full",
                        json.dumps(metadata),
                        "pending",
                        2,  # priority
                        "PII",  # data_classification
                    ))

                    if len(batch_params) >= args.batch_size:
                        await db.executemany(_INSERT_DOC, batch_params)
                        await db.commit()
                        if total % 100_000 == 0:
                            elapsed = time.time() - t0
                            rate = total / elapsed if elapsed > 0 else 0
                            print(f"  Processed: {total:,} ({rate:.0f}/s)")
                        batch_params = []

        if batch_params:
            await db.executemany(_INSERT_DOC, batch_params)
            await db.commit()

        elapsed = time.time() - t0
        print(f"  Cycle {cycle}: {total:,} NC records in {elapsed:.1f}s")
        grand_total += total

    await db.close()
    print(f"\nDone! Inserted {grand_total:,} FEC contribution records")
    print(f"  Database: {db_path}")


if __name__ == "__main__":
    asyncio.run(main())
