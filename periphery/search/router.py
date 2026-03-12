"""Search API endpoints — full-text search across documents, entities, relationships."""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Query

from periphery.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])

_db_path: str = "./data/periphery_documents.db"


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


# ---------------------------------------------------------------------------
# GET /api/search/documents
# ---------------------------------------------------------------------------

@router.get("/documents")
async def search_documents(
    q: str,
    source_feed: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    limit: int = Query(default=25, le=100),
    offset: int = 0,
) -> dict[str, Any]:
    async with get_connection(_db_path) as db:
        # Build WHERE clauses for filters
        filters = []
        params: list[Any] = [q]

        if source_feed:
            filters.append("d.source_feed = ?")
            params.append(source_feed)
        if category:
            filters.append("d.source_category = ?")
            params.append(category)
        if date_from:
            filters.append("d.published >= ?")
            params.append(date_from)
        if date_to:
            filters.append("d.published <= ?")
            params.append(date_to)
        if status:
            filters.append("d.processing_status = ?")
            params.append(status)

        where_clause = ""
        if filters:
            where_clause = "AND " + " AND ".join(filters)

        # Count total matches
        count_sql = f"""
            SELECT COUNT(*) FROM documents_fts fts
            JOIN documents d ON d.rowid = fts.rowid
            WHERE documents_fts MATCH ?
            {where_clause}
        """
        cursor = await db.execute(count_sql, params)
        row = await cursor.fetchone()
        total_count = row[0] if row else 0

        # Fetch results with rank
        search_sql = f"""
            SELECT d.id, d.title, d.url, d.source_feed, d.source_category,
                   d.published, d.processing_status, d.content_quality,
                   d.content, d.summary, fts.rank
            FROM documents_fts fts
            JOIN documents d ON d.rowid = fts.rowid
            WHERE documents_fts MATCH ?
            {where_clause}
            ORDER BY fts.rank
            LIMIT ? OFFSET ?
        """
        params_with_pagination = params + [limit, offset]
        cursor = await db.execute(search_sql, params_with_pagination)
        rows = await cursor.fetchall()

        # Compute rank normalization bounds
        min_rank = 0.0
        max_rank = -1.0
        if rows:
            ranks = [r[10] for r in rows]
            min_rank = min(ranks)
            max_rank = max(ranks)

        results = []
        for row in rows:
            doc_id = row[0]
            content = row[8] or ""
            summary = row[9] or ""
            snippet = summary[:300] if summary else content[:300]
            rank = row[10]

            # Normalize rank to 0-1 (FTS5 rank is negative, more negative = more relevant)
            if max_rank != min_rank:
                relevance = 1.0 - (rank - min_rank) / (max_rank - min_rank)
            else:
                relevance = 1.0

            # Get enrichment counts
            ecursor = await db.execute(
                "SELECT entities, relationships FROM document_enrichments WHERE document_id = ?",
                (doc_id,),
            )
            erow = await ecursor.fetchone()
            entity_count = 0
            relationship_count = 0
            if erow:
                try:
                    import json
                    if erow[0]:
                        ents = json.loads(erow[0]) if isinstance(erow[0], str) else erow[0]
                        entity_count = len(ents) if isinstance(ents, list) else 0
                    if erow[1]:
                        rels = json.loads(erow[1]) if isinstance(erow[1], str) else erow[1]
                        relationship_count = len(rels) if isinstance(rels, list) else 0
                except Exception:
                    pass

            results.append({
                "id": doc_id,
                "title": row[1] or "",
                "url": row[2] or "",
                "source_feed": row[3] or "",
                "source_category": row[4] or "",
                "published": row[5] or "",
                "processing_status": row[6] or "",
                "content_quality": row[7] or "",
                "snippet": snippet,
                "entity_count": entity_count,
                "relationship_count": relationship_count,
                "relevance_score": round(relevance, 4),
            })

        return {
            "results": results,
            "total_count": total_count,
            "offset": offset,
            "query": q,
        }


# ---------------------------------------------------------------------------
# GET /api/search/entities
# ---------------------------------------------------------------------------

@router.get("/entities")
async def search_entities(
    q: str,
    entity_type: str | None = None,
    has_location: bool | None = None,
    min_confidence: float = 0.0,
    limit: int = Query(default=25, le=100),
    offset: int = 0,
) -> dict[str, Any]:
    async with get_connection(_db_path) as db:
        filters = []
        params: list[Any] = [q]

        if entity_type:
            filters.append("ei.entity_type = ?")
            params.append(entity_type)
        if has_location is not None:
            filters.append("ei.has_geospatial = ?")
            params.append(1 if has_location else 0)
        if min_confidence > 0:
            filters.append("ei.confidence >= ?")
            params.append(min_confidence)

        where_extra = ""
        if filters:
            where_extra = "AND " + " AND ".join(filters)

        # Deduplicated aggregation query
        # Group by entity_text (case-insensitive) and aggregate
        agg_sql = f"""
            SELECT
                ei.entity_text,
                ei.entity_type,
                MAX(ei.confidence) as max_confidence,
                COUNT(DISTINCT ei.document_id) as document_count,
                GROUP_CONCAT(DISTINCT ei.source_feed) as source_feeds,
                MIN(ei.published) as first_seen,
                MAX(ei.published) as last_seen,
                MAX(ei.has_geospatial) as has_geo,
                MAX(ei.latitude) as lat,
                MAX(ei.longitude) as lon,
                MAX(ei.location_name) as loc_name,
                MIN(fts.rank) as best_rank
            FROM entities_fts fts
            JOIN entities_index ei ON ei.rowid = fts.rowid
            WHERE entities_fts MATCH ?
            {where_extra}
            GROUP BY ei.entity_text COLLATE NOCASE
            ORDER BY best_rank
        """

        # Get total count first
        count_sql = f"SELECT COUNT(*) FROM ({agg_sql})"
        cursor = await db.execute(count_sql, params)
        row = await cursor.fetchone()
        total_count = row[0] if row else 0

        # Paginated results
        paginated_sql = f"{agg_sql} LIMIT ? OFFSET ?"
        cursor = await db.execute(paginated_sql, params + [limit, offset])
        rows = await cursor.fetchall()

        # Normalize ranks
        min_rank = 0.0
        max_rank = -1.0
        if rows:
            ranks = [r[11] for r in rows]
            min_rank = min(ranks)
            max_rank = max(ranks)

        results = []
        for row in rows:
            rank = row[11]
            if max_rank != min_rank:
                relevance = 1.0 - (rank - min_rank) / (max_rank - min_rank)
            else:
                relevance = 1.0

            feeds = [f for f in (row[4] or "").split(",") if f]
            location = None
            if row[7] and row[8] is not None and row[9] is not None:
                location = {"lat": row[8], "lon": row[9], "name": row[10] or ""}

            results.append({
                "entity_text": row[0],
                "entity_type": row[1],
                "confidence": round(row[2], 4),
                "document_count": row[3],
                "source_feeds": feeds,
                "first_seen": row[5] or "",
                "last_seen": row[6] or "",
                "location": location,
                "relevance_score": round(relevance, 4),
            })

        return {
            "results": results,
            "total_count": total_count,
            "offset": offset,
            "query": q,
        }


# ---------------------------------------------------------------------------
# GET /api/search/relationships
# ---------------------------------------------------------------------------

@router.get("/relationships")
async def search_relationships(
    q: str,
    predicate: str | None = None,
    min_confidence: float = 0.0,
    limit: int = Query(default=25, le=100),
    offset: int = 0,
) -> dict[str, Any]:
    async with get_connection(_db_path) as db:
        filters = []
        params: list[Any] = [q]

        if predicate:
            filters.append("ri.predicate = ?")
            params.append(predicate)
        if min_confidence > 0:
            filters.append("ri.confidence >= ?")
            params.append(min_confidence)

        where_extra = ""
        if filters:
            where_extra = "AND " + " AND ".join(filters)

        agg_sql = f"""
            SELECT
                ri.subject_text,
                ri.predicate,
                ri.object_text,
                MAX(ri.confidence) as max_confidence,
                MAX(ri.extraction_method) as extraction_method,
                COUNT(DISTINCT ri.document_id) as document_count,
                MIN(fts.rank) as best_rank
            FROM relationships_fts fts
            JOIN relationships_index ri ON ri.rowid = fts.rowid
            WHERE relationships_fts MATCH ?
            {where_extra}
            GROUP BY ri.subject_text COLLATE NOCASE, ri.predicate, ri.object_text COLLATE NOCASE
            ORDER BY best_rank
        """

        count_sql = f"SELECT COUNT(*) FROM ({agg_sql})"
        cursor = await db.execute(count_sql, params)
        row = await cursor.fetchone()
        total_count = row[0] if row else 0

        paginated_sql = f"{agg_sql} LIMIT ? OFFSET ?"
        cursor = await db.execute(paginated_sql, params + [limit, offset])
        rows = await cursor.fetchall()

        min_rank = 0.0
        max_rank = -1.0
        if rows:
            ranks = [r[6] for r in rows]
            min_rank = min(ranks)
            max_rank = max(ranks)

        results = []
        for row in rows:
            rank = row[6]
            if max_rank != min_rank:
                relevance = 1.0 - (rank - min_rank) / (max_rank - min_rank)
            else:
                relevance = 1.0

            results.append({
                "subject_text": row[0],
                "predicate": row[1],
                "object_text": row[2],
                "confidence": round(row[3], 4),
                "extraction_method": row[4] or "",
                "document_count": row[5],
                "relevance_score": round(relevance, 4),
            })

        return {
            "results": results,
            "total_count": total_count,
            "offset": offset,
            "query": q,
        }


# ---------------------------------------------------------------------------
# GET /api/search/suggest
# ---------------------------------------------------------------------------

@router.get("/suggest")
async def search_suggest(
    q: str,
    limit: int = Query(default=10, le=50),
) -> dict[str, Any]:
    if len(q) < 2:
        return {"entities": [], "documents": []}

    # Escape FTS5 special characters and add prefix
    safe_q = q.replace('"', '""')
    prefix_query = f'"{safe_q}"*'

    async with get_connection(_db_path) as db:
        # Entity suggestions
        cursor = await db.execute(
            """
            SELECT DISTINCT ei.entity_text, ei.entity_type
            FROM entities_fts fts
            JOIN entities_index ei ON ei.rowid = fts.rowid
            WHERE entities_fts MATCH ?
            ORDER BY fts.rank
            LIMIT ?
            """,
            (prefix_query, limit),
        )
        entity_rows = await cursor.fetchall()

        # Document title suggestions
        cursor = await db.execute(
            """
            SELECT d.id, d.title
            FROM documents_fts fts
            JOIN documents d ON d.rowid = fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY fts.rank
            LIMIT ?
            """,
            (prefix_query, limit),
        )
        doc_rows = await cursor.fetchall()

        return {
            "entities": [{"text": r[0], "type": r[1]} for r in entity_rows],
            "documents": [{"id": r[0], "title": r[1] or ""} for r in doc_rows],
        }


# ---------------------------------------------------------------------------
# GET /api/search/facets
# ---------------------------------------------------------------------------

@router.get("/facets")
async def search_facets(
    q: str | None = None,
) -> dict[str, Any]:
    async with get_connection(_db_path) as db:
        if q:
            # Scoped facets — only count documents matching the query
            base = """
                FROM documents_fts fts
                JOIN documents d ON d.rowid = fts.rowid
                WHERE documents_fts MATCH ?
            """
            base_params: list[Any] = [q]
        else:
            base = "FROM documents d WHERE 1=1"
            base_params = []

        # Source feeds
        cursor = await db.execute(
            f"SELECT d.source_feed, COUNT(*) as cnt {base} AND d.source_feed IS NOT NULL GROUP BY d.source_feed ORDER BY cnt DESC",
            base_params,
        )
        source_feeds = [{"name": r[0], "count": r[1]} for r in await cursor.fetchall()]

        # Categories
        cursor = await db.execute(
            f"SELECT d.source_category, COUNT(*) as cnt {base} AND d.source_category IS NOT NULL GROUP BY d.source_category ORDER BY cnt DESC",
            base_params,
        )
        categories = [{"name": r[0], "count": r[1]} for r in await cursor.fetchall()]

        # Processing statuses
        cursor = await db.execute(
            f"SELECT d.processing_status, COUNT(*) as cnt {base} AND d.processing_status IS NOT NULL GROUP BY d.processing_status ORDER BY cnt DESC",
            base_params,
        )
        processing_statuses = [{"name": r[0], "count": r[1]} for r in await cursor.fetchall()]

        # Date range
        cursor = await db.execute(
            f"SELECT MIN(d.published), MAX(d.published) {base}",
            base_params,
        )
        date_row = await cursor.fetchone()
        date_range = {
            "earliest": (date_row[0] or "") if date_row else "",
            "latest": (date_row[1] or "") if date_row else "",
        }

        # Entity types (from materialized index, not scoped by q for simplicity)
        cursor = await db.execute(
            "SELECT entity_type, COUNT(*) as cnt FROM entities_index GROUP BY entity_type ORDER BY cnt DESC"
        )
        entity_types = [{"name": r[0], "count": r[1]} for r in await cursor.fetchall()]

        return {
            "source_feeds": source_feeds,
            "categories": categories,
            "entity_types": entity_types,
            "processing_statuses": processing_statuses,
            "date_range": date_range,
        }
