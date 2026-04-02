"""SQLite-backed document store for durable persistence (collection DB schema).

Uses the minimal collection DB schema (rss.db / gdelt.db / sanctions.db).
Each collection DB has a single writer process. The enrichment pipeline
reads from these DBs and writes enriched data to analytical.db.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from periphery.db import get_persistent_connection, COLLECTION_SCHEMA_SQL
import structlog

from .models import IngestedDocument

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = Path("./data/rss.db")

_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, content, raw_html, summary, content_quality,
     metadata, classification, enrichment_status, enrichment_priority, ingested_at, content_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class DocumentStore:
    """Async SQLite document store using the collection DB schema."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db: Any = None  # aiosqlite.Connection

    async def initialize(self) -> None:
        """Create database directory, connect, and ensure schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await get_persistent_connection(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(COLLECTION_SCHEMA_SQL)
        await self._db.commit()
        logger.info("document_store_initialized", db_path=str(self._db_path))

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def insert(self, doc: IngestedDocument, *, priority: int | None = None, data_classification: str | None = None) -> bool:
        """Insert a document. Returns True if inserted, False if duplicate."""
        assert self._db is not None
        published_str = doc.published.isoformat() if doc.published else None
        ingested_str = doc.ingested.isoformat() if hasattr(doc, 'ingested') and doc.ingested else datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(doc.metadata) if doc.metadata else None

        # Determine data classification
        if data_classification is None:
            source_type = (doc.metadata or {}).get("source_type", "")
            from periphery.auth.classification import classify_source_type
            data_classification = classify_source_type(source_type).value
            if doc.data_classification and doc.data_classification != "PUBLIC":
                data_classification = doc.data_classification

        # Determine priority
        if priority is None:
            ingest_priority = (doc.metadata or {}).get("ingest_priority")
            if ingest_priority is not None:
                priority = int(ingest_priority)
            else:
                source_type = (doc.metadata or {}).get("source_type", "")
                if source_type == "gdelt_doc":
                    priority = 1
                elif source_type in ("icij_offshore",):
                    priority = 4
                elif doc.source_category == "sanctions_financial":
                    priority = 3
                elif source_type in ("nc_voter", "fec_contributions", "nc_campaign_finance",
                                     "nc_parcels", "irs_exempt_orgs", "nc_sos_business", "nc_rod"):
                    priority = 3
                else:
                    priority = 2

        # Generate content hash for dedup
        import hashlib
        content_hash = None
        if doc.content:
            content_hash = hashlib.sha256(doc.content.encode()).hexdigest()[:32]

        cursor = await self._db.execute(
            _INSERT_DOC,
            (
                doc.id,
                doc.source_feed,
                doc.source_category,
                doc.source_credibility_tier,
                doc.title,
                doc.url,
                published_str,
                doc.content,
                doc.raw_html,
                doc.summary,
                doc.content_quality,
                metadata_json,
                data_classification,
                doc.processing_status if doc.processing_status != "pending" else "pending",
                priority,
                ingested_str,
                content_hash,
            ),
        )
        await self._db.commit()
        inserted = cursor.rowcount > 0
        if inserted:
            logger.debug("document_persisted", doc_id=doc.id, quality=doc.content_quality)
        return inserted

    async def enqueue_for_enrichment(self, doc_id: str) -> None:
        """No-op — enrichment is now driven by enrichment_status polling."""
        pass

    async def exists_by_id(self, doc_id: str) -> bool:
        """Check if a document with this ID exists."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
        )
        return await cursor.fetchone() is not None

    async def exists_by_url(self, url: str) -> bool:
        """Check if a document with this URL exists."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM documents WHERE url = ?", (url,)
        )
        return await cursor.fetchone() is not None

    async def is_duplicate(self, doc_id: str, url: str) -> bool:
        """Check if a document is a duplicate by ID or URL."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM documents WHERE (id = ? OR url = ?) AND enrichment_status != 'pending'",
            (doc_id, url),
        )
        return await cursor.fetchone() is not None

    async def recent_documents(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recently ingested documents."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT id, source_feed, source_category, title, url,
                      published, ingested_at, content_quality, enrichment_status,
                      summary
               FROM documents
               ORDER BY ingested_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        columns = [
            "id", "source_feed", "source_category", "title", "url",
            "published", "ingested_at", "content_quality", "enrichment_status",
            "summary",
        ]
        return [dict(zip(columns, row)) for row in rows]

    async def stats(self) -> dict[str, Any]:
        """Return aggregate statistics about the document store."""
        assert self._db is not None

        cursor = await self._db.execute("SELECT COUNT(*) FROM documents")
        row = await cursor.fetchone()
        total = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT content_quality, COUNT(*) FROM documents GROUP BY content_quality"
        )
        quality_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        cursor = await self._db.execute(
            "SELECT enrichment_status, COUNT(*) FROM documents GROUP BY enrichment_status"
        )
        status_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM documents WHERE ingested_at > datetime('now', '-1 hour')"
        )
        row = await cursor.fetchone()
        last_hour = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM documents WHERE ingested_at > datetime('now', '-1 day')"
        )
        row = await cursor.fetchone()
        last_day = row[0] if row else 0

        return {
            "total_documents": total,
            "content_quality_breakdown": quality_breakdown,
            "enrichment_status_breakdown": status_breakdown,
            "ingested_last_hour": last_hour,
            "ingested_last_day": last_day,
        }
