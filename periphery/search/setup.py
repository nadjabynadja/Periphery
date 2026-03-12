"""Full-text search index setup and maintenance.

Creates FTS5 virtual tables over documents, entities, and relationships
and keeps materialized index tables in sync with enrichment data.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

from periphery.db import get_connection

logger = logging.getLogger(__name__)

# Track last rebuild time to avoid unnecessary work
_last_enrichment_ts: str | None = None


async def initialize_fts(db_path: str) -> None:
    """Create FTS5 virtual tables and sync triggers. Idempotent."""
    async with get_connection(db_path) as db:
        # --- Documents FTS ---
        await db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title,
                content,
                summary,
                source_feed,
                content='documents',
                content_rowid='rowid'
            );

            CREATE TRIGGER IF NOT EXISTS documents_fts_insert AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, content, summary, source_feed)
                VALUES (new.rowid, new.title, new.content, new.summary, new.source_feed);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_fts_delete AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content, summary, source_feed)
                VALUES ('delete', old.rowid, old.title, old.content, old.summary, old.source_feed);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_fts_update AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content, summary, source_feed)
                VALUES ('delete', old.rowid, old.title, old.content, old.summary, old.source_feed);
                INSERT INTO documents_fts(rowid, title, content, summary, source_feed)
                VALUES (new.rowid, new.title, new.content, new.summary, new.source_feed);
            END;
        """)

        # Rebuild from existing data
        await db.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        await db.commit()

        # --- Entities materialized index ---
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS entities_index (
                entity_id TEXT,
                entity_text TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                document_id TEXT NOT NULL,
                source_feed TEXT,
                published TIMESTAMP,
                has_geospatial INTEGER DEFAULT 0,
                latitude REAL,
                longitude REAL,
                location_name TEXT,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            );

            CREATE INDEX IF NOT EXISTS idx_entities_text ON entities_index(entity_text COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities_index(entity_type);
            CREATE INDEX IF NOT EXISTS idx_entities_doc ON entities_index(document_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                entity_text,
                entity_type,
                location_name,
                content='entities_index',
                content_rowid='rowid'
            );
        """)
        await db.commit()

        # --- Relationships materialized index ---
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS relationships_index (
                relationship_id TEXT,
                subject_text TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_text TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                extraction_method TEXT,
                document_id TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            );

            CREATE INDEX IF NOT EXISTS idx_relationships_subject ON relationships_index(subject_text COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_relationships_predicate ON relationships_index(predicate);
            CREATE INDEX IF NOT EXISTS idx_relationships_doc ON relationships_index(document_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS relationships_fts USING fts5(
                subject_text,
                predicate,
                object_text,
                content='relationships_index',
                content_rowid='rowid'
            );
        """)
        await db.commit()

        logger.info("fts_indexes_initialized")


async def rebuild_search_indexes(db_path: str, force: bool = False) -> bool:
    """Rebuild entity and relationship materialized indexes from enrichments.

    Returns True if a rebuild occurred, False if skipped (no new data).
    """
    global _last_enrichment_ts

    async with get_connection(db_path) as db:
        # Check if there are new enrichments since last rebuild
        if not force and _last_enrichment_ts is not None:
            cursor = await db.execute(
                "SELECT MAX(created_at) FROM document_enrichments"
            )
            row = await cursor.fetchone()
            max_ts = row[0] if row else None
            if max_ts is not None and max_ts <= _last_enrichment_ts:
                return False

        # Track current max timestamp
        cursor = await db.execute(
            "SELECT MAX(created_at) FROM document_enrichments"
        )
        row = await cursor.fetchone()
        new_max_ts = row[0] if row else None

        # --- Rebuild entities_index ---
        await db.execute("DELETE FROM entities_index")

        cursor = await db.execute("""
            SELECT de.document_id, de.entities, d.source_feed, d.published
            FROM document_enrichments de
            JOIN documents d ON d.id = de.document_id
            WHERE de.entities IS NOT NULL
        """)
        rows = await cursor.fetchall()

        entity_batch = []
        for row in rows:
            doc_id = row[0]
            entities_json = row[1]
            source_feed = row[2]
            published = row[3]

            if not entities_json:
                continue

            try:
                entities = json.loads(entities_json) if isinstance(entities_json, str) else entities_json
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(entities, list):
                continue

            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                text = entity.get("text", "")
                etype = entity.get("entity_type", entity.get("type", ""))
                if not text or not etype:
                    continue

                confidence = float(entity.get("confidence", 0.0))
                eid = entity.get("id", str(uuid.uuid4())[:8])

                # Extract geospatial data
                has_geo = 0
                lat = None
                lon = None
                loc_name = None
                geo = entity.get("geospatial")
                if isinstance(geo, dict) and geo.get("resolved"):
                    has_geo = 1
                    lat = geo.get("latitude")
                    lon = geo.get("longitude")
                    loc_name = geo.get("location_name", geo.get("name", ""))

                entity_batch.append((
                    eid, text, etype, confidence, doc_id,
                    source_feed, published, has_geo, lat, lon, loc_name,
                ))

        if entity_batch:
            await db.executemany(
                """INSERT INTO entities_index
                   (entity_id, entity_text, entity_type, confidence, document_id,
                    source_feed, published, has_geospatial, latitude, longitude, location_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entity_batch,
            )

        # Rebuild entities FTS
        await db.execute("INSERT INTO entities_fts(entities_fts) VALUES('rebuild')")

        # --- Rebuild relationships_index ---
        await db.execute("DELETE FROM relationships_index")

        cursor = await db.execute("""
            SELECT de.document_id, de.relationships
            FROM document_enrichments de
            WHERE de.relationships IS NOT NULL
        """)
        rows = await cursor.fetchall()

        rel_batch = []
        for row in rows:
            doc_id = row[0]
            rels_json = row[1]

            if not rels_json:
                continue

            try:
                rels = json.loads(rels_json) if isinstance(rels_json, str) else rels_json
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(rels, list):
                continue

            for rel in rels:
                if not isinstance(rel, dict):
                    continue

                subject = rel.get("subject", rel.get("subject_text", ""))
                if isinstance(subject, dict):
                    subject = subject.get("text", subject.get("name", ""))
                predicate = rel.get("predicate", "")
                obj = rel.get("object", rel.get("object_text", ""))
                if isinstance(obj, dict):
                    obj = obj.get("text", obj.get("name", ""))

                if not subject or not predicate or not obj:
                    continue

                confidence = float(rel.get("confidence", 0.0))
                method = rel.get("extraction_method", rel.get("extraction_tier", ""))
                rid = rel.get("id", str(uuid.uuid4())[:8])

                rel_batch.append((rid, subject, predicate, obj, confidence, method, doc_id))

        if rel_batch:
            await db.executemany(
                """INSERT INTO relationships_index
                   (relationship_id, subject_text, predicate, object_text,
                    confidence, extraction_method, document_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rel_batch,
            )

        # Rebuild relationships FTS
        await db.execute("INSERT INTO relationships_fts(relationships_fts) VALUES('rebuild')")

        await db.commit()

        _last_enrichment_ts = new_max_ts
        logger.info(
            "search_indexes_rebuilt entities=%d relationships=%d",
            len(entity_batch), len(rel_batch),
        )
        return True
