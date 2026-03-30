"""Enrichment consumer — drives documents from pending to enriched.

Claims pending documents from collection databases (rss.db, gdelt.db,
sanctions.db), runs them through the enrichment pipeline, writes enrichment
results to the analytical database, and updates enrichment_status back in
the source collection DB.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from periphery.config import get_settings
from periphery.db import get_readonly_connection, get_collection_write_connection, get_connection
import structlog

from periphery.enrichment.models import EnrichedDocument
from periphery.enrichment.pipeline import EnrichmentPipeline
from periphery.rss_ingest.models import IngestedDocument

from .consumer import StageConsumer

logger = structlog.get_logger(__name__)


class EnrichmentConsumer(StageConsumer):
    """Processes documents from pending -> enriching -> enriched.

    Reads from collection DBs, writes enriched data to analytical.db,
    and updates enrichment_status in the source collection DB.

    Implements two-track claiming: reserves a portion of each batch for
    high-priority documents (enrichment_priority <= 2) to ensure fresh
    RSS articles are never starved by the ICIJ backlog.
    """

    input_status = "pending"
    processing_status = "enriching"
    output_status = "enriched"
    started_at_column = "enrichment_started_at"
    completed_at_column = "enrichment_completed_at"

    def __init__(
        self,
        db_path: str,
        pipeline: EnrichmentPipeline | None = None,
        *,
        collection_db_paths: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(db_path, **kwargs)
        self._pipeline = pipeline
        self._collection_db_paths = collection_db_paths or {}
        settings = get_settings()
        self._high_priority_reserved_slots = settings.enrichment_high_priority_reserved_slots

    def set_pipeline(self, pipeline: EnrichmentPipeline) -> None:
        """Set the enrichment pipeline (for deferred initialization)."""
        self._pipeline = pipeline

    async def _claim_batch(self, db: aiosqlite.Connection) -> list[dict[str, Any]]:
        """Two-track claiming from collection databases.

        Reads pending documents from all collection DBs, merges by priority,
        and claims them by updating enrichment_status in the source DB.
        """
        if not self._collection_db_paths:
            # Fallback: read from analytical.db directly (backward compat)
            return await self._claim_batch_from_analytical(db)

        reserved = self._high_priority_reserved_slots
        total = self.batch_size
        all_candidates: list[dict[str, Any]] = []

        # Read pending docs from each collection DB
        for db_name, coll_path in self._collection_db_paths.items():
            try:
                async with get_readonly_connection(coll_path) as coll_db:
                    cursor = await coll_db.execute(
                        """
                        SELECT id, source_feed, source_category, source_credibility_tier,
                               title, url, content, summary, metadata,
                               published, ingested_at, enrichment_priority, content_hash
                        FROM documents
                        WHERE enrichment_status = 'pending'
                        ORDER BY enrichment_priority ASC, ingested_at ASC
                        LIMIT ?
                        """,
                        (total,),  # fetch up to total from each, we'll merge and trim
                    )
                    rows = await cursor.fetchall()
                    for row in rows:
                        doc = dict(row)
                        doc["_source_db"] = db_name
                        doc["_source_db_path"] = coll_path
                        # Map collection schema fields to what process() expects
                        doc["retry_count"] = 0
                        doc["priority"] = doc.get("enrichment_priority", 3)
                        doc["ingested"] = doc.get("ingested_at")
                        all_candidates.append(doc)
            except Exception:
                logger.exception("collection_db_read_failed", db_name=db_name, path=coll_path)

        if not all_candidates:
            return []

        # Two-track selection: high-priority first, then fill remaining
        high_priority = [d for d in all_candidates if (d.get("priority") or 3) <= 2]
        low_priority = [d for d in all_candidates if (d.get("priority") or 3) > 2]

        # Sort each track
        high_priority.sort(key=lambda d: (d.get("priority", 3), d.get("ingested_at", "")))
        low_priority.sort(key=lambda d: (d.get("priority", 3), d.get("ingested_at", "")))

        selected: list[dict[str, Any]] = []
        selected.extend(high_priority[:reserved])
        remaining = total - len(selected)
        if remaining > 0:
            # Fill from low priority, excluding already-selected IDs
            selected_ids = {d["id"] for d in selected}
            for doc in low_priority:
                if doc["id"] not in selected_ids:
                    selected.append(doc)
                    if len(selected) >= total:
                        break

        if not selected:
            return []

        # Claim documents by updating enrichment_status in their source collection DBs
        now = datetime.now(timezone.utc).isoformat()
        by_source: dict[str, list[str]] = {}
        for doc in selected:
            source = doc["_source_db_path"]
            by_source.setdefault(source, []).append(doc["id"])

        for source_path, doc_ids in by_source.items():
            try:
                async with get_collection_write_connection(source_path) as coll_db:
                    placeholders = ",".join("?" for _ in doc_ids)
                    await coll_db.execute(
                        f"""
                        UPDATE documents
                        SET enrichment_status = 'enriching'
                        WHERE id IN ({placeholders})
                          AND enrichment_status = 'pending'
                        """,
                        doc_ids,
                    )
                    await coll_db.commit()
            except Exception:
                logger.exception("collection_db_claim_failed", path=source_path)

        hp_count = sum(1 for d in selected if (d.get("priority") or 3) <= 2)
        logger.debug(
            "enrichment_batch_claimed",
            count=len(selected),
            high_priority=hp_count,
            low_priority=len(selected) - hp_count,
            sources={name: len([d for d in selected if d.get("_source_db") == name])
                     for name in self._collection_db_paths},
        )
        return selected

    async def _claim_batch_from_analytical(self, db: aiosqlite.Connection) -> list[dict[str, Any]]:
        """Fallback: claim from analytical.db for backward compatibility."""
        now = datetime.now(timezone.utc).isoformat()
        reserved = self._high_priority_reserved_slots
        total = self.batch_size

        all_docs: list[dict[str, Any]] = []

        if reserved > 0:
            cursor = await db.execute(
                """
                SELECT id, source_feed, source_category, source_credibility_tier,
                       title, url, content, summary, metadata, retry_count,
                       published, ingested, priority
                FROM documents
                WHERE processing_status = ? AND COALESCE(priority, 3) <= 2
                ORDER BY COALESCE(priority, 3) ASC, COALESCE(source_credibility_tier, 4) ASC, ingested ASC
                LIMIT ?
                """,
                (self.input_status, reserved),
            )
            rows = await cursor.fetchall()
            for row in rows:
                all_docs.append(dict(row))

        remaining = total - len(all_docs)
        if remaining > 0:
            already_claimed_ids = [d["id"] for d in all_docs]
            if already_claimed_ids:
                placeholders = ",".join("?" for _ in already_claimed_ids)
                cursor = await db.execute(
                    f"""
                    SELECT id, source_feed, source_category, source_credibility_tier,
                           title, url, content, summary, metadata, retry_count,
                           published, ingested, priority
                    FROM documents
                    WHERE processing_status = ? AND id NOT IN ({placeholders})
                    ORDER BY COALESCE(priority, 3) ASC, COALESCE(source_credibility_tier, 4) ASC, ingested ASC
                    LIMIT ?
                    """,
                    [self.input_status, *already_claimed_ids, remaining],
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT id, source_feed, source_category, source_credibility_tier,
                           title, url, content, summary, metadata, retry_count,
                           published, ingested, priority
                    FROM documents
                    WHERE processing_status = ?
                    ORDER BY COALESCE(priority, 3) ASC, COALESCE(source_credibility_tier, 4) ASC, ingested ASC
                    LIMIT ?
                    """,
                    (self.input_status, remaining),
                )
            rows = await cursor.fetchall()
            for row in rows:
                all_docs.append(dict(row))

        if not all_docs:
            return []

        doc_ids = [d["id"] for d in all_docs]
        placeholders = ",".join("?" for _ in doc_ids)

        set_clause = "processing_status = ?"
        params: list[Any] = [self.processing_status]
        if self.started_at_column:
            set_clause += f", {self.started_at_column} = ?"
            params.append(now)

        params.extend(doc_ids)
        params.append(self.input_status)

        await db.execute(
            f"""
            UPDATE documents
            SET {set_clause}
            WHERE id IN ({placeholders})
              AND processing_status = ?
            """,
            params,
        )
        await db.commit()
        return all_docs

    async def process(
        self, db: aiosqlite.Connection, doc_rows: list[dict[str, Any]]
    ) -> list[str]:
        """Run enrichment pipeline on each claimed document."""
        if self._pipeline is None:
            logger.warning("enrichment_pipeline_not_configured")
            return []

        success_ids: list[str] = []

        for doc_row in doc_rows:
            try:
                enriched = await self._enrich_document(doc_row)
                metadata = doc_row.get("metadata")
                if isinstance(metadata, str):
                    metadata = json.loads(metadata) if metadata else {}
                await self._store_enrichment(db, enriched, metadata=metadata)

                # Also insert/update the document in analytical.db's documents table
                await self._upsert_document_in_analytical(db, doc_row)

                success_ids.append(doc_row["id"])
            except Exception:
                logger.exception(
                    "enrichment_failed",
                    doc_id=doc_row["id"],
                )

        return success_ids

    async def _advance(self, db: aiosqlite.Connection, doc_id: str) -> None:
        """Advance a document: update analytical.db AND source collection DB."""
        now = datetime.now(timezone.utc).isoformat()

        # Update analytical.db processing_status
        set_clause = "processing_status = ?"
        params: list[Any] = [self.output_status]
        if self.completed_at_column:
            set_clause += f", {self.completed_at_column} = ?"
            params.append(now)

        params.append(doc_id)
        await db.execute(
            f"UPDATE documents SET {set_clause} WHERE id = ?",
            params,
        )
        await db.commit()

        # Update enrichment_status in the source collection DB
        # Find which source DB this doc came from
        source_db_path = self._find_source_db_for_doc(doc_id)
        if source_db_path:
            try:
                async with get_collection_write_connection(source_db_path) as coll_db:
                    await coll_db.execute(
                        "UPDATE documents SET enrichment_status = 'enriched' WHERE id = ?",
                        (doc_id,),
                    )
                    await coll_db.commit()
            except Exception:
                logger.exception("collection_db_advance_failed", doc_id=doc_id, path=source_db_path)

        # Notify the next stage consumer
        if self._on_advance is not None:
            try:
                self._on_advance(doc_id)
            except Exception:
                pass

    async def _handle_failure(
        self, db: aiosqlite.Connection, doc_id: str, retry_count: int, error: str
    ) -> None:
        """Handle processing failure — update both analytical.db and source collection DB."""
        retry_count = retry_count or 0
        new_retry = retry_count + 1
        self._error_count_last_hour += 1

        if new_retry >= self._max_retries:
            await db.execute(
                """
                UPDATE documents
                SET processing_status = 'failed',
                    processing_error = ?,
                    retry_count = ?
                WHERE id = ?
                """,
                (error, new_retry, doc_id),
            )
            logger.warning(
                "document_failed_permanently",
                consumer=self.name,
                doc_id=doc_id,
                error=error,
                retries=new_retry,
            )
            # Mark as failed in collection DB too
            source_db_path = self._find_source_db_for_doc(doc_id)
            if source_db_path:
                try:
                    async with get_collection_write_connection(source_db_path) as coll_db:
                        await coll_db.execute(
                            "UPDATE documents SET enrichment_status = 'failed' WHERE id = ?",
                            (doc_id,),
                        )
                        await coll_db.commit()
                except Exception:
                    pass
        else:
            await db.execute(
                """
                UPDATE documents
                SET processing_status = ?,
                    retry_count = ?,
                    processing_error = ?
                WHERE id = ?
                """,
                (self.input_status, new_retry, error, doc_id),
            )
            # Reset to pending in collection DB for retry
            source_db_path = self._find_source_db_for_doc(doc_id)
            if source_db_path:
                try:
                    async with get_collection_write_connection(source_db_path) as coll_db:
                        await coll_db.execute(
                            "UPDATE documents SET enrichment_status = 'pending' WHERE id = ?",
                            (doc_id,),
                        )
                        await coll_db.commit()
                except Exception:
                    pass
            logger.info(
                "document_retry_scheduled",
                consumer=self.name,
                doc_id=doc_id,
                retry=new_retry,
                max_retries=self._max_retries,
            )
        await db.commit()

    def _find_source_db_for_doc(self, doc_id: str) -> str | None:
        """Find which collection DB a document came from.

        Uses the _claimed_sources cache populated during _claim_batch.
        """
        return self._claimed_sources.get(doc_id)

    async def _run_cycle(self) -> int:
        """Override to track source DB paths for claimed docs."""
        # Phase 1: Claim batch
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            claimed = await self._claim_batch(db)
            if not claimed:
                return 0

        # Cache source DB paths for advance/failure handling
        self._claimed_sources: dict[str, str] = {}
        for doc in claimed:
            if "_source_db_path" in doc:
                self._claimed_sources[doc["id"]] = doc["_source_db_path"]

        # Phase 2: Process
        import time
        start = time.monotonic()
        try:
            async with get_connection(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                db.row_factory = aiosqlite.Row
                success_ids = await self.process(db, claimed)
        except Exception as exc:
            async with get_connection(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                db.row_factory = aiosqlite.Row
                for doc in claimed:
                    await self._handle_failure(db, doc["id"], doc.get("retry_count", 0), str(exc))
            return 0

        elapsed = time.monotonic() - start

        # Phase 3: Advance/fail
        if success_ids is None:
            success_ids = []
        success_set = set(str(sid) for sid in success_ids)

        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            for doc in claimed:
                doc_id = str(doc["id"])
                if doc_id in success_set:
                    await self._advance(db, doc_id)
                    self._docs_processed_times.append(elapsed / max(len(success_set), 1))
                    self._docs_processed_last_hour += 1
                else:
                    await self._handle_failure(
                        db, doc_id, doc.get("retry_count", 0),
                        "not in success list from process()"
                    )

            return len(success_ids)

    async def _enrich_document(self, doc_row: dict[str, Any]) -> EnrichedDocument:
        """Convert a DB row to IngestedDocument and run through pipeline."""
        metadata = doc_row.get("metadata")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}

        ingested_doc = IngestedDocument(
            id=doc_row["id"],
            source_feed=doc_row["source_feed"],
            source_category=doc_row.get("source_category", ""),
            source_credibility_tier=doc_row.get("source_credibility_tier", 3),
            title=doc_row.get("title", ""),
            url=doc_row.get("url", ""),
            content=doc_row.get("content", ""),
            summary=doc_row.get("summary", ""),
            metadata=metadata,
        )

        return await self._pipeline.process_document(ingested_doc)

    async def _store_enrichment(
        self, db: aiosqlite.Connection, enriched: EnrichedDocument,
        metadata: dict | None = None,
    ) -> None:
        """Write enrichment results to document_enrichments table in analytical.db."""
        entities_json = json.dumps(
            [e.model_dump(mode="json") for e in enriched.entities]
        )
        relationships_json = json.dumps(
            [r.model_dump(mode="json") for r in enriched.relationships]
        )
        metadata_json = json.dumps(enriched.metadata.model_dump(mode="json"))

        await db.execute(
            """
            INSERT OR REPLACE INTO document_enrichments
                (document_id, entities, relationships, enrichment_metadata)
            VALUES (?, ?, ?, ?)
            """,
            (enriched.id, entities_json, relationships_json, metadata_json),
        )

        # Populate spatial_observations for source documents with coordinates
        if metadata:
            await self._store_spatial_observation(db, enriched.id, metadata)

        await db.commit()
        logger.debug("enrichment_stored", doc_id=enriched.id)

    async def _upsert_document_in_analytical(
        self, db: aiosqlite.Connection, doc_row: dict[str, Any]
    ) -> None:
        """Insert or update the document record in analytical.db's documents table.

        Maps collection DB fields to the full analytical schema.
        """
        metadata = doc_row.get("metadata")
        if isinstance(metadata, str):
            pass  # already JSON string
        elif metadata is not None:
            metadata = json.dumps(metadata)

        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            """
            INSERT OR REPLACE INTO documents
                (id, source_feed, source_category, source_credibility_tier,
                 title, url, published, ingested, content, raw_html, summary,
                 content_quality, metadata, processing_status, priority,
                 data_classification, enrichment_started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_row["id"],
                doc_row.get("source_feed", ""),
                doc_row.get("source_category", ""),
                doc_row.get("source_credibility_tier", 3),
                doc_row.get("title", ""),
                doc_row.get("url", ""),
                doc_row.get("published"),
                doc_row.get("ingested_at") or doc_row.get("ingested") or now,
                doc_row.get("content", ""),
                doc_row.get("raw_html", ""),
                doc_row.get("summary", ""),
                doc_row.get("content_quality", "full"),
                metadata,
                "enriching",  # will be advanced to 'enriched' by _advance
                doc_row.get("enrichment_priority") or doc_row.get("priority", 3),
                doc_row.get("classification") or doc_row.get("data_classification", "PUBLIC"),
                now,
            ),
        )
        await db.commit()

    async def _store_spatial_observation(
        self,
        db: aiosqlite.Connection,
        doc_id: str,
        metadata: dict,
    ) -> None:
        """Insert a spatial observation row when source metadata has coordinates."""
        lat = metadata.get("latitude")
        lon = metadata.get("longitude")
        source_type = metadata.get("source_type", "")
        if lat is None or lon is None or not source_type:
            return

        try:
            lat, lon = float(lat), float(lon)
        except (ValueError, TypeError):
            return

        entity_id = (
            metadata.get("icao24")
            or metadata.get("mmsi")
            or metadata.get("norad_id")
            or metadata.get("osm_id")
            or metadata.get("camera_id")
            or ""
        )
        entity_name = (
            metadata.get("callsign")
            or metadata.get("vessel_name")
            or metadata.get("name")
            or metadata.get("camera_name")
            or ""
        )
        if isinstance(entity_name, str):
            entity_name = entity_name.strip()

        obs_id = hashlib.sha256(
            f"{doc_id}:{source_type}:{entity_id}".encode()
        ).hexdigest()[:24]

        observed_at = metadata.get("api_time")
        if observed_at is None:
            observed_at = datetime.now(timezone.utc).isoformat()

        obs_meta = {}
        for key in (
            "origin_country", "on_ground", "squawk", "destination",
            "flag", "nav_status", "vessel_type", "feature_type",
            "camera_type", "status", "osm_type", "tags",
        ):
            val = metadata.get(key)
            if val is not None:
                obs_meta[key] = val

        await db.execute(
            """
            INSERT OR REPLACE INTO spatial_observations
                (observation_id, document_id, source_type, entity_id,
                 entity_name, latitude, longitude, altitude_m, speed_kts,
                 heading_deg, observed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obs_id,
                doc_id,
                source_type,
                str(entity_id),
                entity_name,
                lat,
                lon,
                metadata.get("baro_altitude_m") or metadata.get("geo_altitude_m"),
                metadata.get("speed_kts") or (
                    metadata.get("velocity_ms") * 1.94384
                    if metadata.get("velocity_ms") is not None else None
                ),
                metadata.get("heading_deg") or metadata.get("true_track_deg") or metadata.get("course_deg"),
                observed_at,
                json.dumps(obs_meta) if obs_meta else None,
            ),
        )
