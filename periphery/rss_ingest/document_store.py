"""SQLite-backed document store for durable persistence.

Every document that enters the polling loop gets written here before the
daemon moves on.  Uses aiosqlite for async-compatible access so it doesn't
block the polling loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from periphery.db import get_connection
from periphery.db import get_persistent_connection
import structlog

from .models import IngestedDocument

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = Path("./data/periphery_documents.db")

_CREATE_DOCUMENTS = """
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
    enrichment_started_at TIMESTAMP,
    enrichment_completed_at TIMESTAMP,
    embedding_started_at TIMESTAMP,
    embedding_completed_at TIMESTAMP,
    crystallization_started_at TIMESTAMP,
    crystallization_completed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    priority INTEGER DEFAULT 3
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ingested ON documents(ingested)",
    "CREATE INDEX IF NOT EXISTS idx_source_feed ON documents(source_feed)",
    "CREATE INDEX IF NOT EXISTS idx_processing_status ON documents(processing_status)",
    "CREATE INDEX IF NOT EXISTS idx_url ON documents(url)",
    "CREATE INDEX IF NOT EXISTS idx_content_quality ON documents(content_quality)",
    "CREATE INDEX IF NOT EXISTS idx_priority ON documents(priority)",
]

_CREATE_DOCUMENT_ENRICHMENTS = """
CREATE TABLE IF NOT EXISTS document_enrichments (
    document_id TEXT PRIMARY KEY REFERENCES documents(id),
    entities JSON,
    relationships JSON,
    temporal_context JSON,
    geospatial_data JSON,
    cross_references JSON,
    enrichment_metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_DOCUMENT_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS document_embeddings (
    document_id TEXT PRIMARY KEY REFERENCES documents(id),
    semantic_embedding BLOB,
    semantic_chunks JSON,
    entity_embedding BLOB,
    relational_embedding BLOB,
    temporal_vector JSON,
    geospatial_vector JSON,
    embedding_model TEXT,
    embedding_dimensions INTEGER,
    completeness JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
)
"""

_INSERT_DOC = """
INSERT OR IGNORE INTO documents
    (id, source_feed, source_category, source_credibility_tier, title, url,
     published, ingested, content, raw_html, summary, content_quality,
     metadata, processing_status, priority)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class DocumentStore:
    """Async SQLite document store."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database directory, connect, and ensure schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await get_persistent_connection(self._db_path)
        # Enable WAL mode for better concurrent read/write performance
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_DOCUMENTS)
        for idx_sql in _CREATE_INDEXES:
            await self._db.execute(idx_sql)
        await self._db.execute(_CREATE_DOCUMENT_ENRICHMENTS)
        await self._db.execute(_CREATE_DOCUMENT_EMBEDDINGS)
        # Migrate legacy schema: rename old columns if they exist
        await self._migrate_legacy_columns()
        await self._migrate_embeddings_schema()
        await self._migrate_priority_column()
        await self._db.commit()
        logger.info("document_store_initialized", db_path=str(self._db_path))

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def insert(self, doc: IngestedDocument, *, priority: int | None = None) -> bool:
        """Insert a document. Returns True if inserted, False if duplicate."""
        assert self._db is not None
        published_str = doc.published.isoformat() if doc.published else None
        ingested_str = doc.ingested.isoformat()
        metadata_json = json.dumps(doc.metadata) if doc.metadata else None

        # Determine priority: explicit param > source-based heuristic > default 3
        if priority is None:
            source_type = (doc.metadata or {}).get("source_type", "")
            if source_type in ("icij_offshore",):
                priority = 3  # ICIJ historical = low priority
            elif doc.source_category == "sanctions_financial":
                priority = 3
            else:
                priority = 1  # RSS articles = high priority

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
                ingested_str,
                doc.content,
                doc.raw_html,
                doc.summary,
                doc.content_quality,
                metadata_json,
                "pending",
                priority,
            ),
        )
        await self._db.commit()
        inserted = cursor.rowcount > 0
        if inserted:
            logger.debug("document_persisted", doc_id=doc.id, quality=doc.content_quality)
        return inserted

    async def enqueue_for_enrichment(self, doc_id: str) -> None:
        """No-op — enrichment is now driven by processing_status polling."""
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
        """Check if a document is a duplicate by content hash or URL."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM documents WHERE (id = ? OR url = ?) AND processing_status != 'pending'",
            (doc_id, url),
        )
        return await cursor.fetchone() is not None

    async def recent_documents(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recently ingested documents."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT id, source_feed, source_category, title, url,
                      published, ingested, content_quality, processing_status,
                      summary
               FROM documents
               ORDER BY ingested DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        columns = [
            "id", "source_feed", "source_category", "title", "url",
            "published", "ingested", "content_quality", "processing_status",
            "summary",
        ]
        return [dict(zip(columns, row)) for row in rows]

    async def stats(self) -> dict[str, Any]:
        """Return aggregate statistics about the document store."""
        assert self._db is not None

        # total count
        cursor = await self._db.execute("SELECT COUNT(*) FROM documents")
        row = await cursor.fetchone()
        total = row[0] if row else 0

        # by content_quality
        cursor = await self._db.execute(
            "SELECT content_quality, COUNT(*) FROM documents GROUP BY content_quality"
        )
        quality_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        # by processing_status
        cursor = await self._db.execute(
            "SELECT processing_status, COUNT(*) FROM documents GROUP BY processing_status"
        )
        status_breakdown = {r[0]: r[1] for r in await cursor.fetchall()}

        # ingested in last hour
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 hour')"
        )
        row = await cursor.fetchone()
        last_hour = row[0] if row else 0

        # ingested in last day
        cursor = await self._db.execute(
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

    async def _migrate_legacy_columns(self) -> None:
        """Migrate from old enrichment_status/embedding_status to processing_status.

        SQLite doesn't support DROP COLUMN before 3.35.0, so we detect legacy
        columns via PRAGMA and migrate data if needed.
        """
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in await cursor.fetchall()}

        if "enrichment_status" in columns and "processing_status" not in columns:
            # Old schema — add new columns
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN processing_status TEXT DEFAULT 'pending'"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN processing_error TEXT"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN enrichment_started_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN enrichment_completed_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN embedding_started_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN embedding_completed_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN crystallization_started_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN crystallization_completed_at TIMESTAMP"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN retry_count INTEGER DEFAULT 0"
            )
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN max_retries INTEGER DEFAULT 3"
            )
            # Map old statuses to new state machine
            await self._db.execute("""
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

    async def _migrate_embeddings_schema(self) -> None:
        """Add multi-space embedding columns if they don't exist yet."""
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(document_embeddings)")
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
                await self._db.execute(
                    f"ALTER TABLE document_embeddings ADD COLUMN {col_name} {col_type}"
                )
                logger.info("embeddings_schema_migrated", column=col_name)

    async def _migrate_priority_column(self) -> None:
        """Add priority column to documents if it doesn't exist."""
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in await cursor.fetchall()}

        if "priority" not in columns:
            await self._db.execute(
                "ALTER TABLE documents ADD COLUMN priority INTEGER DEFAULT 3"
            )
            logger.info("priority_column_added")
