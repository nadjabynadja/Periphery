"""ICIJ Offshore Leaks data source.

Downloads and parses the ICIJ Offshore Leaks database (Panama Papers,
Paradise Papers, Pandora Papers, etc.) from the ICIJ data portal.

The ZIP archive contains CSV files for offshore entities, officers,
intermediaries, addresses, and their relationships.

LICENSE / ATTRIBUTION
---------------------
The ICIJ Offshore Leaks Database is dual-licensed:

  * Database structure: Open Database License (ODbL) v1.0
    https://opendatacommons.org/licenses/odbl/1-0/
  * Database contents:  Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA)
    https://creativecommons.org/licenses/by-sa/3.0/

Both licences allow commercial use. Both require attribution.

Required attribution text:
    "Includes data from the ICIJ Offshore Leaks Database, licensed under
     ODbL v1.0 (database structure) and CC BY-SA 3.0 (contents).
     © International Consortium of Investigative Journalists."
    https://offshoreleaks.icij.org/

ODbL carve-out: serving query results through a web interface does NOT
constitute "conveying" the database under ODbL §4.6, so our SaaS delivery
model is compliant as long as we display the attribution above in the UI
and include attribution metadata in API responses that surface ICIJ data.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import os
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

ICIJ_ZIP_URL = "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip"

# CSV file names within the ZIP
_CSV_ENTITIES = "nodes-entities.csv"
_CSV_OFFICERS = "nodes-officers.csv"
_CSV_INTERMEDIARIES = "nodes-intermediaries.csv"
_CSV_ADDRESSES = "nodes-addresses.csv"
_CSV_RELATIONSHIPS = "relationships.csv"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _build_entity_content(row: dict[str, str], node_type: str) -> str:
    """Build structured text content for an entity document."""
    parts = []
    name = row.get("name", "").strip() or row.get("original_name", "").strip()
    if name:
        parts.append(f"Name: {name}")

    if node_type == "entity":
        for field, label in [
            ("jurisdiction", "Jurisdiction"),
            ("jurisdiction_description", "Jurisdiction (full)"),
            ("incorporation_date", "Incorporation date"),
            ("inactivation_date", "Inactivation date"),
            ("struck_off_date", "Struck off date"),
            ("status", "Status"),
            ("company_type", "Company type"),
            ("service_provider", "Service provider"),
            ("countries", "Countries"),
            ("sourceID", "Source dataset"),
        ]:
            val = row.get(field, "").strip()
            if val and val not in ("-", "N/A"):
                parts.append(f"{label}: {val}")

    elif node_type == "officer":
        for field, label in [
            ("countries", "Countries"),
            ("valid_until", "Valid until"),
            ("note", "Note"),
            ("sourceID", "Source dataset"),
        ]:
            val = row.get(field, "").strip()
            if val and val not in ("-", "N/A"):
                parts.append(f"{label}: {val}")

    elif node_type == "intermediary":
        for field, label in [
            ("countries", "Countries"),
            ("status", "Status"),
            ("valid_until", "Valid until"),
            ("note", "Note"),
            ("sourceID", "Source dataset"),
        ]:
            val = row.get(field, "").strip()
            if val and val not in ("-", "N/A"):
                parts.append(f"{label}: {val}")

    return "\n".join(parts)


class ICIJOffshoreSource(DataSource):
    """Polls ICIJ Offshore Leaks database for offshore entities.

    Downloads the full ZIP archive and parses entities, officers, and
    intermediaries into IngestedDocument objects compatible with the
    standard enrichment pipeline.
    """

    name = "icij_offshore"
    category = "sanctions_financial"
    default_poll_interval = 604800  # 7 days

    def __init__(
        self,
        *,
        poll_interval: int | None = None,
        enabled: bool = True,
        node_types: list[str] | None = None,
        data_dir: str | Path = "/app/data",
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._node_types = node_types or ["entities", "officers", "intermediaries"]
        self._data_dir = Path(data_dir)
        self._seen_hashes: set[str] = set()

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Download and parse ICIJ Offshore Leaks ZIP, return new documents."""
        logger.info("icij_offshore_fetch_start", url=ICIJ_ZIP_URL)

        zip_path = self._data_dir / "icij_offshore_full.zip"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Stream download to disk — ZIP is ~500MB+
        await self._download_zip(session, zip_path)

        docs = await asyncio.get_event_loop().run_in_executor(
            None, self._parse_zip, zip_path
        )

        logger.info(
            "icij_offshore_fetch_complete",
            total_docs=len(docs),
            node_types=self._node_types,
        )
        return docs

    async def _download_zip(
        self, session: aiohttp.ClientSession, dest: Path
    ) -> None:
        """Stream download ZIP to disk."""
        logger.info("icij_download_start", dest=str(dest))
        # Use a longer timeout for the huge file
        timeout = aiohttp.ClientTimeout(total=7200, connect=30)
        async with session.get(ICIJ_ZIP_URL, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1 MB
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        if downloaded % (50 * 1024 * 1024) < 1024 * 1024:
                            logger.info(
                                "icij_download_progress",
                                pct=pct,
                                mb=downloaded // (1024 * 1024),
                            )
        logger.info("icij_download_done", size_mb=dest.stat().st_size // (1024 * 1024))

    def _parse_zip(self, zip_path: Path) -> list[IngestedDocument]:
        """Parse ZIP contents synchronously (runs in thread executor)."""
        docs: list[IngestedDocument] = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Build relationships index first
            relationships: dict[str, list[dict[str, str]]] = defaultdict(list)
            if _CSV_RELATIONSHIPS in zf.namelist():
                relationships = self._load_relationships(zf)

            # Parse each requested node type
            type_map = {
                "entities": (_CSV_ENTITIES, "entity"),
                "officers": (_CSV_OFFICERS, "officer"),
                "intermediaries": (_CSV_INTERMEDIARIES, "intermediary"),
            }
            for node_type_key in self._node_types:
                csv_file, node_type = type_map.get(node_type_key, (None, None))
                if csv_file is None:
                    logger.warning("icij_unknown_node_type", node_type=node_type_key)
                    continue
                if csv_file not in zf.namelist():
                    logger.warning("icij_csv_not_found", csv_file=csv_file)
                    continue

                type_docs = self._parse_node_csv(
                    zf, csv_file, node_type, relationships
                )
                docs.extend(type_docs)
                logger.info(
                    "icij_node_type_parsed",
                    node_type=node_type,
                    count=len(type_docs),
                )

        return docs

    def _load_relationships(
        self, zf: zipfile.ZipFile
    ) -> dict[str, list[dict[str, str]]]:
        """Load relationships indexed by node_id."""
        rels: dict[str, list[dict[str, str]]] = defaultdict(list)
        with zf.open(_CSV_RELATIONSHIPS) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                node_1 = row.get("node_id_1", "").strip()
                node_2 = row.get("node_id_2", "").strip()
                rel_type = row.get("rel_type", "").strip()
                rel_info = {
                    "rel_type": rel_type,
                    "node_id_1": node_1,
                    "node_id_2": node_2,
                    "link": row.get("link", "").strip(),
                    "start_date": row.get("start_date", "").strip(),
                    "end_date": row.get("end_date", "").strip(),
                    "sourceID": row.get("sourceID", "").strip(),
                }
                if node_1:
                    rels[node_1].append(rel_info)
                if node_2 and node_2 != node_1:
                    rels[node_2].append(rel_info)
        return rels

    def _parse_node_csv(
        self,
        zf: zipfile.ZipFile,
        csv_file: str,
        node_type: str,
        relationships: dict[str, list[dict[str, str]]],
    ) -> list[IngestedDocument]:
        """Parse a node CSV file into IngestedDocument objects."""
        docs: list[IngestedDocument] = []

        with zf.open(csv_file) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                node_id = row.get("node_id", "").strip()
                name = (
                    row.get("name", "").strip()
                    or row.get("original_name", "").strip()
                    or f"Unknown {node_type}"
                )
                if not node_id:
                    continue

                content = _build_entity_content(row, node_type)
                content_hash = _content_hash(content)

                # Deduplication — skip if content unchanged
                if content_hash in self._seen_hashes:
                    continue
                self._seen_hashes.add(content_hash)

                doc_id = make_document_id("icij_offshore", node_id)

                # Build metadata preserving all CSV fields
                metadata: dict[str, Any] = {k: v for k, v in row.items() if v.strip()}
                metadata["source_type"] = "icij_offshore"
                metadata["node_type"] = node_type
                metadata["content_hash"] = content_hash
                # ODbL v1.0 / CC BY-SA 3.0 attribution (required by licence)
                metadata["attribution"] = "International Consortium of Investigative Journalists (ICIJ)"
                metadata["license"] = "ODbL-1.0 / CC-BY-SA-3.0"
                metadata["source_url"] = "https://offshoreleaks.icij.org/"

                # Attach relationships
                node_rels = relationships.get(node_id, [])
                if node_rels:
                    metadata["relationships"] = node_rels

                # Country/jurisdiction fields
                countries = row.get("countries", "").strip()
                jurisdiction = row.get("jurisdiction", "").strip()
                if countries:
                    metadata["country_codes"] = [
                        c.strip() for c in countries.split(";") if c.strip()
                    ]
                if jurisdiction:
                    metadata["jurisdiction_code"] = jurisdiction

                source_id = row.get("sourceID", "").strip()

                doc = IngestedDocument(
                    id=doc_id,
                    source_feed="ICIJ Offshore Leaks",
                    source_category="sanctions_financial",
                    source_credibility_tier=2,
                    title=name,
                    url=f"https://offshoreleaks.icij.org/nodes/{node_id}",
                    published=None,
                    content=content,
                    metadata=metadata,
                )
                docs.append(doc)

        return docs
