"""Enrichment consumer — drives documents from pending to enriched.

Claims pending documents, runs them through the enrichment pipeline
(entity extraction, relationship extraction, temporal tagging, geospatial
resolution, source credibility, entity resolution), and writes enrichment
results to the document_enrichments table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from periphery.config import get_settings
from periphery.db import get_connection
import structlog

from periphery.enrichment.models import EnrichedDocument
from periphery.enrichment.pipeline import EnrichmentPipeline
from periphery.rss_ingest.models import IngestedDocument

from .consumer import StageConsumer

logger = structlog.get_logger(__name__)


class EnrichmentConsumer(StageConsumer):
    """Processes documents from pending -> enriching -> enriched.

    Implements two-track claiming: reserves a portion of each batch for
    high-priority documents (priority <= 2) to ensure fresh RSS articles
    are never starved by the ICIJ backlog.
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
        **kwargs: Any,
    ) -> None:
        super().__init__(db_path, **kwargs)
        self._pipeline = pipeline
        settings = get_settings()
        self._high_priority_reserved_slots = settings.enrichment_high_priority_reserved_slots

    def set_pipeline(self, pipeline: EnrichmentPipeline) -> None:
        """Set the enrichment pipeline (for deferred initialization)."""
        self._pipeline = pipeline

    async def _claim_batch(self, db: aiosqlite.Connection) -> list[dict[str, Any]]:
        """Two-track claiming: high-priority first, then fill remaining slots."""
        now = datetime.now(timezone.utc).isoformat()
        reserved = self._high_priority_reserved_slots
        total = self.batch_size

        all_docs: list[dict[str, Any]] = []

        # Track 1: Claim up to `reserved` high-priority docs (priority <= 2)
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

        # Track 2: Fill remaining slots from any priority
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

        # Claim them in a transaction
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

        hp_count = sum(1 for d in all_docs if (d.get("priority") or 3) <= 2)
        logger.debug(
            "enrichment_batch_claimed",
            count=len(all_docs),
            high_priority=hp_count,
            low_priority=len(all_docs) - hp_count,
            doc_ids=doc_ids[:3],
        )
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
                # Pass original metadata so spatial observations can be stored
                metadata = doc_row.get("metadata")
                if isinstance(metadata, str):
                    metadata = json.loads(metadata) if metadata else {}
                await self._store_enrichment(db, enriched, metadata=metadata)
                success_ids.append(doc_row["id"])
            except Exception:
                logger.exception(
                    "enrichment_failed",
                    doc_id=doc_row["id"],
                )

        return success_ids

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
        """Write enrichment results to document_enrichments table."""
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

        # Derive entity_id and entity_name from source-specific fields
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

        import hashlib
        obs_id = hashlib.sha256(
            f"{doc_id}:{source_type}:{entity_id}".encode()
        ).hexdigest()[:24]

        observed_at = metadata.get("api_time")
        if observed_at is None:
            from datetime import datetime, timezone
            observed_at = datetime.now(timezone.utc).isoformat()

        # Extra observation metadata (altitude, speed, heading, etc.)
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
