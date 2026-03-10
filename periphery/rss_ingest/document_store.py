"""SQLite-backed document store for durable persistence.

Every document that enters the polling loop gets written here before the
daemon moves on.  Uses the shared DatabasePool for connection management.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from periphery.db import get_pool, get_persistent_connection
import structlog

from .models import IngestedDocument

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = Path("./data/periphery_documents.db")

_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class DocumentStore:
    """Async SQLite document store backed by the shared connection pool."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db = None  # Only used for persistent-connection fallback

    async def initialize(self) -> None:
        """Ensure schema exists and pool is ready.

        Schema is now managed centrally by db.py — we just verify the pool
        is available and run any needed column migrations.
        """
        try:
            pool = get_pool()
            async with pool.acquire() as db:
                await self._migrate_legacy_columns(db)
                await self._migrate_embeddings_schema(db)
                await db.commit()
        except RuntimeError:
            # Pool not yet initialized — fall back to persistent connection
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await get_persistent_connection(self._db_path)
            await self._migrate_legacy_columns(self._db)
            await self._migrate_embeddings_schema(self._db)
            await self._db.commit()

        logger.info("document_store_initialized", db_path=str(self._db_path))

    async def close(self) -> None:
        """Close the fallback persistent connection if used."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _get_db(self):
        """Get a database connection — pool-backed or persistent fallback."""
        if self._db is not None:
            return self._db
        # When using the pool, callers must use the context manager directly.
        # This method is only for the persistent-connection path.
        raise RuntimeError("Use pool.acquire() context manager instead")

    async def insert(self, doc: IngestedDocument) -> bool:
        """Insert a document. Returns True if inserted, False if duplicate."""
        published_str = doc.published.isoformat() if doc.published else None
        ingested_str = doc.ingested.isoformat()
        metadata_json = json.dumps(doc.metadata) if doc.metadata else None

        params = (
            doc.id,
            doc.source_feed,
            doc.source_category,
            doc.source_credibility_tier,
            doc.title,
            doc.url,
            published_str,
            ingested_str,
            doc.content,
            doc.raw_html,
            doc.summary,
            doc.content_quality,
            metadata_json,
            "pending",
        )

        if self._db is not None:
            cursor = await self._db.execute(_INSERT_DOC, params)
            await self._db.commit()
            inserted = cursor.rowcount > 0
        else:
            pool = get_pool()
            async with pool.acquire() as db:
                cursor = await db.execute(_INSERT_DOC, params)
                await db.commit()
                inserted = cursor.rowcount > 0

        if inserted:
            logger.debug("document_persisted", doc_id=doc.id, quality=doc.content_quality)
        return inserted

    async def enqueue_for_enrichment(self, doc_id: str) -> None:
        """No-op — enrichment is now driven by processing_status polling."""
        pass

    async def exists_by_id(self, doc_id: str) -> bool:
        """Check if a document with this ID exists."""
        if self._db is not None:
            cursor = await self._db.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            )
            return await cursor.fetchone() is not None

        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            )
            return await cursor.fetchone() is not None

    async def exists_by_url(self, url: str) -> bool:
        """Check if a document with this URL exists."""
        if self._db is not None:
            cursor = await self._db.execute(
                "SELECT 1 FROM documents WHERE url = ?", (url,)
            )
            return await cursor.fetchone() is not None

        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT 1 FROM documents WHERE url = ?", (url,)
            )
            return await cursor.fetchone() is not None

    async def is_duplicate(self, doc_id: str, url: str) -> bool:
        """Check if a document is a duplicate by content hash or URL."""
        query = "SELECT 1 FROM documents WHERE (id = ? OR url = ?) AND processing_status != 'pending'"
        if self._db is not None:
            cursor = await self._db.execute(query, (doc_id, url))
            return await cursor.fetchone() is not None

        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(query, (doc_id, url))
            return await cursor.fetchone() is not None

    async def recent_documents(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recently ingested documents."""
        query = """SELECT id, source_feed, source_category, title, url,
                          published, ingested, content_quality, processing_status,
                          summary
                   FROM documents
                   ORDER BY ingested DESC
                   LIMIT ?"""
        columns = [
            "id", "source_feed", "source_category", "title", "url",
            "published", "ingested", "content_quality", "processing_status",
            "summary",
        ]

        if self._db is not None:
            cursor = await self._db.execute(query, (limit,))
            rows = await cursor.fetchall()
        else:
            pool = get_pool()
            async with pool.acquire() as db:
                cursor = await db.execute(query, (limit,))
                rows = await cursor.fetchall()

        return [dict(zip(columns, row)) for row in rows]

    async def stats(self) -> dict[str, Any]:
        """Return aggregate statistics about the document store."""
        if self._db is not None:
            return await self._stats_impl(self._db)

        pool = get_pool()
        async with pool.acquire() as db:
            return await self._stats_impl(db)

    async def _stats_impl(self, db) -> dict[str, Any]:
        cursor = await db.execute("SELECT COUNT(*) FROM documents")
        row = await cursor.fetchone()
        total = row[0] if row else 0

        cursor = await db.execute(
            "SELECT content_quality, COUNT(*) FROM documents GROUP BY content_quality"
        )
        quality_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        cursor = await db.execute(
            "SELECT processing_status, COUNT(*) FROM documents GROUP BY processing_status"
        )
        status_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        cursor = await db.execute(
            "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 hour')"
        )
        row = await cursor.fetchone()
        last_hour = row[0] if row else 0

        cursor = await db.execute(
            "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 day')"
        )
        row = await cursor.fetchone()
        last_day = row[0] if row else 0

        return {
            "total_documents": total,
            "content_quality_breakdown": quality_breakdown,
            "processing_status_breakdown": status_breakdown,
            "ingested_last_hour": last_hour,
            "ingested_last_day": last_day,
        }

    async def _migrate_legacy_columns(self, db) -> None:
        """Migrate from old enrichment_status/embedding_status to processing_status."""
        cursor = await db.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in await cursor.fetchall()}

        if "enrichment_status" in columns and "processing_status" not in columns:
            await db.execute(
                "ALTER TABLE documents ADD COLUMN processing_status TEXT DEFAULT 'pending'"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN processing_error TEXT"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN enrichment_started_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN enrichment_completed_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN embedding_started_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN embedding_completed_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN crystallization_started_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN crystallization_completed_at TIMESTAMP"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN retry_count INTEGER DEFAULT 0"
            )
            await db.execute(
                "ALTER TABLE documents ADD COLUMN max_retries INTEGER DEFAULT 3"
            )
            await db.execute("""
                UPDATE documents SET processing_status = CASE
                    WHEN enrichment_status = 'complete' AND embedding_status = 'complete'
                        THEN 'embedded'
                    WHEN enrichment_status = 'complete'
                        THEN 'enriched'
                    WHEN enrichment_status = 'failed' OR embedding_status = 'failed'
                        THEN 'failed'
                    ELSE 'pending'
                END
            """)
            logger.info("migrated_legacy_status_columns")

    async def _migrate_embeddings_schema(self, db) -> None:
        """Add multi-space embedding columns if they don't exist yet."""
        cursor = await db.execute("PRAGMA table_info(document_embeddings)")
        columns = {row[1] for row in await cursor.fetchall()}

        new_cols = {
            "semantic_chunks": "JSON",
            "relational_embedding": "BLOB",
            "temporal_vector": "JSON",
            "geospatial_vector": "JSON",
            "completeness": "JSON",
            "updated_at": "TIMESTAMP",
        }
        for col_name, col_type in new_cols.items():
            if col_name not in columns:
                await db.execute(
                    f"ALTER TABLE document_embeddings ADD COLUMN {col_name} {col_type}"
                )
                logger.info("embeddings_schema_migrated", column=col_name)
