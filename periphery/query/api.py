"""Component 6 — API Endpoints for the Query Interface.

Exposes the full analytical query pipeline, ontology snapshot, entity
detail, cluster detail, query history, streaming, and analyst annotations
through a FastAPI router.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from periphery.config import get_settings

settings = get_settings()
from periphery.db import get_connection
from periphery.query.models import (
    AnalyticalQueryRequest,
    AnalyticalQueryResponse,
    StreamUpdate,
)
from periphery.query.renderer import ConfidenceRenderer, confidence_to_rendering

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query-interface"])

# Set by main.py during initialization
_analytical_engine = None
_crystallizer_worker = None
_entity_index = None

# Module-level cache for enrichment data, keyed by snapshot_id
_enrichment_cache: dict[str, dict[str, Any]] = {}


def _check_admin_key(x_admin_key: str | None) -> None:
    """Raise HTTP 403 if admin key is missing or incorrect."""
    _settings = get_settings()
    if not _settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled (admin_api_key not configured)")
    if x_admin_key != _settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")


class FeedbackRequest(BaseModel):
    """Schema for analyst feedback on a query result."""
    rating: int | None = None  # e.g. 1 (thumbs up) or -1 (thumbs down)
    notes: str | None = None
    tags: list[str] | None = None


class AnnotationRequest(BaseModel):
    """Schema for analyst annotations (entity merge, relationship confirm/deny, etc.)."""
    annotation_type: str  # entity_merge | relationship_confirm | relationship_deny
    target_type: str      # entity | relationship | cluster
    target_id: str
    data: dict[str, Any] = {}
    session_id: str = ""


async def _load_enrichment_entities(
    member_doc_ids: list[str],
    cluster_doc_map: dict[str, list[str]],
    snapshot_id: str,
    generated_at_iso: str,
    include_rendering: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load entities and relationships from document_enrichments table.

    Returns (entities, relationships) lists built from real enrichment data.
    Raises Exception if enrichment data is unavailable.
    """
    # Check cache first
    if snapshot_id in _enrichment_cache:
        cached = _enrichment_cache[snapshot_id]
        return cached["entities"], cached["relationships"]

    # Clear stale cache entries (keep only current snapshot)
    _enrichment_cache.clear()

    db_path = settings.pipeline_db_path

    # Collect all enrichment rows
    doc_entities: dict[str, list] = {}
    doc_relationships: dict[str, list] = {}

    async with get_connection(db_path) as db:
        # Process in batches of 500
        batch_size = 500
        for i in range(0, len(member_doc_ids), batch_size):
            batch = member_doc_ids[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            cursor = await db.execute(
                f"""
                SELECT document_id, entities, relationships
                FROM document_enrichments
                WHERE document_id IN ({placeholders})
                """,
                batch,
            )
            for row in await cursor.fetchall():
                did = row[0]
                entities_raw = []
                rels_raw = []

                if row[1]:
                    try:
                        entities_raw = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    except (json.JSONDecodeError, TypeError):
                        pass

                if row[2]:
                    try:
                        rels_raw = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                    except (json.JSONDecodeError, TypeError):
                        pass

                doc_entities[did] = entities_raw if isinstance(entities_raw, list) else []
                doc_relationships[did] = rels_raw if isinstance(rels_raw, list) else []

    # Build deduplicated entity list — resolve through entity index if available
    # key by canonical_id (from entity index) for proper deduplication
    best_entity: dict[str, dict] = {}  # canonical_id -> best entity dict
    entity_doc_count: dict[str, int] = {}  # canonical_id -> document count
    entity_cluster_ids: dict[str, set] = {}  # canonical_id -> set of cluster IDs

    # Build reverse map: doc_id -> list of cluster_ids
    doc_to_clusters: dict[str, list[str]] = {}
    for cluster_id, doc_ids in cluster_doc_map.items():
        for did in doc_ids:
            doc_to_clusters.setdefault(did, []).append(cluster_id)

    for did, ents in doc_entities.items():
        for ent in ents:
            if not isinstance(ent, dict) or "text" not in ent:
                continue
            text = ent["text"].strip()
            if not text:
                continue

            # Resolve through entity index for canonical deduplication
            resolved_id = ent.get("canonical_id", text)
            resolved_name = text
            resolved_aliases: list[str] = []
            resolved_type = ent.get("entity_type", "entity")

            if _entity_index is not None:
                idx_entity = _entity_index.lookup_exact(text)
                if idx_entity is None:
                    idx_entity = _entity_index.lookup_alias(text)
                if idx_entity is not None:
                    resolved_id = idx_entity.canonical_id
                    resolved_name = idx_entity.canonical_name
                    resolved_aliases = idx_entity.aliases
                    resolved_type = idx_entity.entity_type

            key = resolved_id

            # Track document count
            entity_doc_count[key] = entity_doc_count.get(key, 0) + 1

            # Track cluster IDs
            if key not in entity_cluster_ids:
                entity_cluster_ids[key] = set()
            for cid in doc_to_clusters.get(did, []):
                entity_cluster_ids[key].add(cid)

            # Keep highest-confidence version
            new_conf = ent.get("confidence", 0.0)
            if key not in best_entity or new_conf > best_entity[key].get("confidence", 0.0):
                best_entity[key] = {
                    **ent,
                    "canonical_id": resolved_id,
                    "text": resolved_name,
                    "entity_type": resolved_type,
                    "aliases": resolved_aliases,
                }

    # Build final entity list
    entities: list[dict[str, Any]] = []
    # Also build a lookup: canonical_id -> True for relationship filtering
    entity_canonical_ids: set[str] = set()

    for key, ent in best_entity.items():
        canonical_id = ent.get("canonical_id", ent["text"])
        entity_canonical_ids.add(canonical_id)
        # Also add the text as a canonical_id for relationship matching
        entity_canonical_ids.add(ent["text"])

        geo = ent.get("geospatial")
        location = None
        if (
            isinstance(geo, dict)
            and geo.get("resolved")
            and geo.get("latitude") is not None
        ):
            location = {
                "lat": geo["latitude"],
                "lon": geo["longitude"],
            }

        # Check entity index for location data if not in enrichment
        if location is None and _entity_index is not None:
            extras = _entity_index._db_extras.get(canonical_id, {})
            if extras.get("location_lat") is not None:
                location = {
                    "lat": extras["location_lat"],
                    "lon": extras["location_lon"],
                }

        entity_conf = ent.get("confidence", 0.5)
        rendering = confidence_to_rendering(entity_conf).model_dump() if include_rendering else {}

        # Use entity index data for first_seen/last_seen if available
        first_seen = generated_at_iso
        last_seen = generated_at_iso
        if _entity_index is not None:
            idx_entity = _entity_index.get(canonical_id)
            if idx_entity is not None:
                first_seen = idx_entity.first_seen.isoformat() if idx_entity.first_seen else generated_at_iso
                last_seen = idx_entity.last_seen.isoformat() if idx_entity.last_seen else generated_at_iso

        entities.append({
            "canonical_id": canonical_id,
            "name": ent["text"],
            "entity_type": ent.get("entity_type", "entity"),
            "aliases": ent.get("aliases", []),
            "confidence": entity_conf,
            "source_count": entity_doc_count.get(key, 1),
            "cluster_ids": sorted(entity_cluster_ids.get(key, set())),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "location": location,
            "rendering": rendering,
        })

    # Build deduplicated relationship list
    relationships: list[dict[str, Any]] = []
    seen_rels: set[tuple] = set()

    for did, rels in doc_relationships.items():
        for i, rel in enumerate(rels):
            if not isinstance(rel, dict):
                continue
            subj_id = rel.get("subject_id", "")
            obj_id = rel.get("object_id", "")
            predicate = rel.get("predicate", "related_to")

            # Only include if both endpoints are in entity list
            if subj_id not in entity_canonical_ids or obj_id not in entity_canonical_ids:
                continue

            dedup_key = (subj_id, predicate, obj_id)
            if dedup_key in seen_rels:
                continue
            seen_rels.add(dedup_key)

            temporal = rel.get("temporal_context")
            temporal_status = (
                temporal.get("status", "current")
                if isinstance(temporal, dict)
                else "current"
            )

            relationships.append({
                "id": f"rel-{subj_id[:8]}-{obj_id[:8]}-{len(relationships)}",
                "subject_id": subj_id,
                "predicate": predicate,
                "object_id": obj_id,
                "confidence": rel.get("confidence", 0.5),
                "evidence_sentences": [],
                "temporal_context": temporal_status,
                "extraction_tier": rel.get("extraction_method", "co_occurrence"),
                "source_count": 1,
                "first_seen": generated_at_iso,
                "last_seen": generated_at_iso,
            })

    # Sort entities by source_count (most-referenced first) — no cap, pagination handles volume
    entities.sort(key=lambda e: e.get("source_count", 0), reverse=True)

    # Sort relationships by confidence — no cap, pagination handles volume
    relationships.sort(key=lambda r: r.get("confidence", 0), reverse=True)

    # Cache results
    _enrichment_cache[snapshot_id] = {
        "entities": entities,
        "relationships": relationships,
    }

    return entities, relationships


def set_analytical_engine(engine) -> None:
    global _analytical_engine
    _analytical_engine = engine


def set_crystallizer_worker(worker) -> None:
    global _crystallizer_worker
    _crystallizer_worker = worker


def set_entity_index(index) -> None:
    global _entity_index
    _entity_index = index


def _get_engine():
    assert _analytical_engine is not None, "Analytical query engine not initialized"
    return _analytical_engine


# ── Primary Query Endpoint ───────────────────────────────────────────────


@router.post("/query", response_model=AnalyticalQueryResponse)
async def analytical_query(request: AnalyticalQueryRequest):
    """Execute a natural language analytical query against the living ontology.

    This is the primary query endpoint. It resolves analytical intent,
    executes multi-space retrieval, synthesizes results into a coherent
    narrative, and returns everything with legibility gradient rendering.
    """
    engine = _get_engine()
    return await engine.query(request)


# ── Ontology Snapshot Endpoint ───────────────────────────────────────────


@router.get("/snapshot")
async def get_snapshot(
    confidence_floor: float = Query(0.0, ge=0.0, le=1.0),
    cluster_ids: str | None = Query(None, description="Comma-separated cluster IDs"),
    include_rendering: bool = Query(True),
):
    """Return the current ontology snapshot for frontend graph rendering.

    Filters by confidence floor and optional cluster IDs.
    """
    engine = _get_engine()
    snapshot = engine.snapshot
    if snapshot is None and _crystallizer_worker is not None:
        snapshot = _crystallizer_worker.current_snapshot
    if snapshot is None:
        return {
            "snapshot_id": None,
            "generated_at": None,
            "clusters": [],
            "entities": [],
        }

    renderer = ConfidenceRenderer()
    filter_ids = set(cluster_ids.split(",")) if cluster_ids else None

    # Filter clusters
    clusters = snapshot.clusters
    if filter_ids:
        clusters = [c for c in clusters if c.cluster_id in filter_ids]
    if confidence_floor > 0:
        clusters = [c for c in clusters if c.confidence >= confidence_floor]

    # Filter trajectories to matching clusters
    cluster_id_set = {c.cluster_id for c in clusters}
    trajectories = [
        t for t in snapshot.trajectories
        if t.cluster_id in cluster_id_set
    ]

    # Filter anomalies
    anomalies = [a for a in snapshot.anomalies if not a.resolved]
    if confidence_floor > 0:
        anomalies = [a for a in anomalies if a.anomaly_score >= confidence_floor]

    # Filter gradients to matching clusters
    gradients = [
        g for g in snapshot.relational_gradients
        if g.source_cluster in cluster_id_set or g.target_cluster in cluster_id_set
    ]

    # Warm the enrichment cache if not already loaded, so /api/entities is fast on first call
    try:
        all_member_doc_ids: list[str] = []
        cluster_doc_map: dict[str, list[str]] = {}
        seen_doc_ids: set[str] = set()
        for c in clusters:
            cluster_doc_map[c.cluster_id] = c.member_document_ids
            for did in c.member_document_ids:
                if did not in seen_doc_ids:
                    seen_doc_ids.add(did)
                    all_member_doc_ids.append(did)

        if all_member_doc_ids and snapshot.snapshot_id not in _enrichment_cache:
            await _load_enrichment_entities(
                member_doc_ids=all_member_doc_ids,
                cluster_doc_map=cluster_doc_map,
                snapshot_id=snapshot.snapshot_id,
                generated_at_iso=snapshot.generated_at.isoformat(),
                include_rendering=include_rendering,
            )
            logger.info("snapshot_enrichment_cache_warmed snapshot_id=%s", snapshot.snapshot_id)
    except Exception:
        logger.warning(
            "snapshot_enrichment_cache_warm_failed", exc_info=True,
        )

    # Get total counts from corpus_stats or enrichment cache
    cached = _enrichment_cache.get(snapshot.snapshot_id)
    total_entities = len(cached["entities"]) if cached else snapshot.corpus_stats.total_entities
    total_relationships = len(cached["relationships"]) if cached else snapshot.corpus_stats.total_relationships

    result: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "timestamp": snapshot.generated_at.isoformat(),
        "corpus_stats": snapshot.corpus_stats.model_dump(),
        "total_entities": total_entities,
        "total_relationships": total_relationships,
        "entity_count": total_entities,
        "relationship_count": total_relationships,
        "cluster_count": len(clusters),
    }

    # Flatten cluster/trajectory/anomaly data for frontend consumption
    result["clusters"] = [
        {
            **(c.model_dump(mode="json")),
            "member_ids": c.member_document_ids,
            **({"rendering": confidence_to_rendering(c.confidence).model_dump()} if include_rendering else {}),
        }
        for c in clusters
    ]
    result["trajectories"] = [
        {
            **(t.model_dump(mode="json")),
            **({"rendering": confidence_to_rendering(t.confidence).model_dump()} if include_rendering else {}),
        }
        for t in trajectories
    ]
    result["anomalies"] = [
        {
            **(a.model_dump(mode="json")),
            **({"rendering": confidence_to_rendering(min(1.0, a.anomaly_score)).model_dump()} if include_rendering else {}),
        }
        for a in anomalies
    ]
    result["gradients"] = [
        {
            "source_cluster_id": g.source_cluster,
            "target_cluster_id": g.target_cluster,
            "score": g.gradient_score,
            "relationship_count": len(g.components.bridge_entities) if g.components else 0,
            "key_relationships": [],
            **({"rendering": confidence_to_rendering(g.gradient_score).model_dump()} if include_rendering else {}),
        }
        for g in gradients
    ]
    result["emerging_structures"] = [
        {
            "structure_id": e.region_id if hasattr(e, 'region_id') else str(i),
            "member_ids": e.candidate_entities if hasattr(e, 'candidate_entities') else [],
            "formation_progress": e.formation_confidence,
            "potential_label": getattr(e, 'label', ""),
            "detected_at": e.detected_at.isoformat() if hasattr(e, 'detected_at') and hasattr(e.detected_at, 'isoformat') else "",
            **({"rendering": confidence_to_rendering(e.formation_confidence).model_dump()} if include_rendering else {}),
        }
        for i, e in enumerate(snapshot.emerging_structures)
    ]

    return result


# ── Paginated Entity Endpoint ───────────────────────────────────────────


@router.get("/entities")
async def get_entities(
    page: int = Query(1, ge=1),
    limit: int = Query(200, ge=1, le=1000),
    cluster_id: str | None = Query(None),
    entity_type: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("source_count"),
    include_rendering: bool = Query(False),
):
    """Return paginated entities from the enrichment cache.

    Filters by cluster_id, entity_type, and text search on name/aliases.
    Supports sort_by: source_count (default), confidence, name.
    """
    snapshot = None
    if _analytical_engine is not None:
        snapshot = _analytical_engine.snapshot
    if snapshot is None and _crystallizer_worker is not None:
        snapshot = _crystallizer_worker.current_snapshot

    if snapshot is None:
        return {"total": 0, "page": page, "limit": limit, "entities": []}

    # Ensure cache is populated
    if snapshot.snapshot_id not in _enrichment_cache:
        # Build cache from all clusters
        all_member_doc_ids: list[str] = []
        cluster_doc_map: dict[str, list[str]] = {}
        seen_doc_ids: set[str] = set()
        for c in snapshot.clusters:
            cluster_doc_map[c.cluster_id] = c.member_document_ids
            for did in c.member_document_ids:
                if did not in seen_doc_ids:
                    seen_doc_ids.add(did)
                    all_member_doc_ids.append(did)

        if all_member_doc_ids:
            await _load_enrichment_entities(
                member_doc_ids=all_member_doc_ids,
                cluster_doc_map=cluster_doc_map,
                snapshot_id=snapshot.snapshot_id,
                generated_at_iso=snapshot.generated_at.isoformat(),
                include_rendering=include_rendering,
            )

    cached = _enrichment_cache.get(snapshot.snapshot_id)
    if not cached:
        return {"total": 0, "page": page, "limit": limit, "entities": []}

    entities = cached["entities"]

    # Apply filters
    if cluster_id is not None:
        entities = [e for e in entities if cluster_id in e.get("cluster_ids", [])]

    if entity_type is not None:
        entity_type_lower = entity_type.lower()
        entities = [e for e in entities if e.get("entity_type", "").lower() == entity_type_lower]

    if search is not None:
        search_lower = search.lower()
        filtered = []
        for e in entities:
            if search_lower in e.get("name", "").lower():
                filtered.append(e)
                continue
            if any(search_lower in alias.lower() for alias in e.get("aliases", [])):
                filtered.append(e)
        entities = filtered

    # Apply sorting
    if sort_by == "confidence":
        entities = sorted(entities, key=lambda e: e.get("confidence", 0), reverse=True)
    elif sort_by == "name":
        entities = sorted(entities, key=lambda e: e.get("name", "").lower())
    else:  # default: source_count
        entities = sorted(entities, key=lambda e: e.get("source_count", 0), reverse=True)

    total = len(entities)
    offset = (page - 1) * limit
    page_entities = entities[offset: offset + limit]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "entities": page_entities,
    }


# ── Paginated Relationship Endpoint ─────────────────────────────────────


@router.get("/relationships")
async def get_relationships(
    page: int = Query(1, ge=1),
    limit: int = Query(200, ge=1, le=1000),
    entity_id: str | None = Query(None),
    cluster_id: str | None = Query(None),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    sort_by: str = Query("confidence"),
    include_rendering: bool = Query(False),
):
    """Return paginated relationships from the enrichment cache.

    Filters by entity_id (subject or object), cluster_id, and min_confidence.
    Supports sort_by: confidence (default).
    """
    snapshot = None
    if _analytical_engine is not None:
        snapshot = _analytical_engine.snapshot
    if snapshot is None and _crystallizer_worker is not None:
        snapshot = _crystallizer_worker.current_snapshot

    if snapshot is None:
        return {"total": 0, "page": page, "limit": limit, "relationships": []}

    # Ensure cache is populated
    if snapshot.snapshot_id not in _enrichment_cache:
        all_member_doc_ids: list[str] = []
        cluster_doc_map: dict[str, list[str]] = {}
        seen_doc_ids: set[str] = set()
        for c in snapshot.clusters:
            cluster_doc_map[c.cluster_id] = c.member_document_ids
            for did in c.member_document_ids:
                if did not in seen_doc_ids:
                    seen_doc_ids.add(did)
                    all_member_doc_ids.append(did)

        if all_member_doc_ids:
            await _load_enrichment_entities(
                member_doc_ids=all_member_doc_ids,
                cluster_doc_map=cluster_doc_map,
                snapshot_id=snapshot.snapshot_id,
                generated_at_iso=snapshot.generated_at.isoformat(),
                include_rendering=include_rendering,
            )

    cached = _enrichment_cache.get(snapshot.snapshot_id)
    if not cached:
        return {"total": 0, "page": page, "limit": limit, "relationships": []}

    relationships = cached["relationships"]

    # Apply filters
    if entity_id is not None:
        relationships = [
            r for r in relationships
            if r.get("subject_id") == entity_id or r.get("object_id") == entity_id
        ]

    if cluster_id is not None:
        # Build set of entity canonical_ids in this cluster from cache
        cluster_entity_ids: set[str] = set()
        for e in cached["entities"]:
            if cluster_id in e.get("cluster_ids", []):
                cluster_entity_ids.add(e["canonical_id"])
        relationships = [
            r for r in relationships
            if r.get("subject_id") in cluster_entity_ids or r.get("object_id") in cluster_entity_ids
        ]

    if min_confidence is not None:
        relationships = [r for r in relationships if r.get("confidence", 0) >= min_confidence]

    # Apply sorting
    if sort_by == "confidence":
        relationships = sorted(relationships, key=lambda r: r.get("confidence", 0), reverse=True)

    total = len(relationships)
    offset = (page - 1) * limit
    page_rels = relationships[offset: offset + limit]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "relationships": page_rels,
    }


# ── Entity Detail Endpoint ───────────────────────────────────────────────


@router.get("/entity/{canonical_id}")
async def get_entity(canonical_id: str):
    """Return full entity profile with relationships and cluster memberships."""
    engine = _get_engine()

    # Resolve entity through the index
    entity_index = _entity_index
    if entity_index is None:
        return {"error": "Entity index not available", "entity": None}

    canonical = entity_index.get(canonical_id)
    if canonical is None:
        canonical = entity_index.lookup_exact(canonical_id)
    if canonical is None:
        canonical = entity_index.lookup_alias(canonical_id)
    if canonical is None:
        canonical, _score = entity_index.lookup_fuzzy(canonical_id, "")

    if canonical is None:
        return {"error": "Entity not found", "entity": None}

    # Get full profile
    profile = await entity_index.get_profile(canonical.canonical_id)

    # Find cluster memberships from snapshot
    snapshot = engine.snapshot
    cluster_memberships = []
    if snapshot:
        entity_names = {canonical.canonical_name.lower()} | {a.lower() for a in canonical.aliases}
        for cluster in snapshot.clusters:
            cluster_entity_names = {e.lower() for e in cluster.key_entities}
            if entity_names & cluster_entity_names:
                cluster_memberships.append({
                    "cluster_id": cluster.cluster_id,
                    "label": cluster.label,
                    "confidence": cluster.confidence,
                    "size": cluster.size,
                    "role": "member",
                })

    # Fetch relationships from document_enrichments
    entity_relationships = []
    if profile and profile.get("source_documents"):
        try:
            async with get_connection() as db:
                doc_ids = profile["source_documents"][:50]
                placeholders = ",".join("?" for _ in doc_ids)
                cursor = await db.execute(
                    f"SELECT document_id, relationships FROM document_enrichments WHERE document_id IN ({placeholders})",
                    doc_ids,
                )
                seen_rels: set[str] = set()
                entity_names_lower = {canonical.canonical_name.lower()} | {a.lower() for a in canonical.aliases}
                for row in await cursor.fetchall():
                    try:
                        rels = json.loads(row[1]) if row[1] else []
                    except (json.JSONDecodeError, TypeError):
                        continue
                    for rel in rels:
                        if not isinstance(rel, dict):
                            continue
                        subj = rel.get("subject_text", rel.get("subject_id", ""))
                        obj = rel.get("object_text", rel.get("object_id", ""))
                        pred = rel.get("predicate", "")
                        if subj.lower() in entity_names_lower or obj.lower() in entity_names_lower:
                            rel_key = f"{subj}|{pred}|{obj}"
                            if rel_key not in seen_rels:
                                seen_rels.add(rel_key)
                                direction = "outgoing" if subj.lower() in entity_names_lower else "incoming"
                                other_name = obj if direction == "outgoing" else subj
                                entity_relationships.append({
                                    "relationship_id": f"rel-{hashlib.sha256(rel_key.encode()).hexdigest()[:8]}",
                                    "predicate": pred,
                                    "other_entity_name": other_name,
                                    "direction": direction,
                                    "confidence": rel.get("confidence", 0.5),
                                    "temporal_context": rel.get("temporal_context", {}).get("status", "current") if isinstance(rel.get("temporal_context"), dict) else "current",
                                    "evidence_sentence": rel.get("evidence", ""),
                                    "extraction_tier": rel.get("extraction_method", "co_occurrence"),
                                })
        except Exception:
            logger.debug("entity_relationships_fetch_failed", exc_info=True)

    # Fetch source documents metadata
    source_docs = []
    if profile and profile.get("source_documents"):
        try:
            async with get_connection() as db:
                doc_ids = profile["source_documents"][:20]
                placeholders = ",".join("?" for _ in doc_ids)
                cursor = await db.execute(
                    f"SELECT id, title, source_feed, published, content_quality FROM documents WHERE id IN ({placeholders})",
                    doc_ids,
                )
                for row in await cursor.fetchall():
                    entry: dict = {
                        "document_id": row[0],
                        "title": row[1] or "Untitled",
                        "source": row[2] or "",
                        "date": str(row[3] or ""),
                        "content_quality": row[4] or "full",
                    }
                    # ODbL v1.0 / CC BY-SA 3.0 — attach attribution for ICIJ data
                    if (row[2] or "").startswith("ICIJ"):
                        entry["attribution"] = "International Consortium of Investigative Journalists (ICIJ)"
                        entry["license"] = "ODbL-1.0 / CC-BY-SA-3.0"
                        entry["source_url"] = "https://offshoreleaks.icij.org/"
                    source_docs.append(entry)
        except Exception:
            logger.debug("entity_source_docs_fetch_failed", exc_info=True)

    # Build temporal history
    temporal_history = []
    if profile and profile.get("source_documents"):
        try:
            async with get_connection() as db:
                doc_ids = profile["source_documents"]
                placeholders = ",".join("?" for _ in doc_ids)
                cursor = await db.execute(
                    f"""SELECT DATE(published) as day, COUNT(*) as cnt
                         FROM documents
                         WHERE id IN ({placeholders}) AND published IS NOT NULL
                        GROUP BY DATE(published)
                         ORDER BY day""",
                    doc_ids,
                )
                for row in await cursor.fetchall():
                    temporal_history.append({"date": row[0], "count": row[1]})
        except Exception:
            pass

    bio_short = profile.get("bio_short") if profile else None
    bio_long = profile.get("bio_long") if profile else None

    return {
        "entity": {
            "canonical_id": canonical.canonical_id,
            "name": canonical.canonical_name,
            "entity_type": canonical.entity_type,
            "confidence": canonical.merge_confidence,
            "aliases": canonical.aliases,
            "bio_short": bio_short,
            "bio_long": bio_long,
            "cluster_memberships": cluster_memberships,
            "relationships": entity_relationships,
            "source_documents": source_docs,
            "temporal_history": temporal_history,
            "confidence_explanation": {
                "factors": [
                    {"name": "Merge confidence", "score": canonical.merge_confidence, "weight": 0.3, "description": "How confidently this entity was resolved across mentions"},
                    {"name": "Source diversity", "score": min(1.0, len(canonical.source_documents) / 10), "weight": 0.3, "description": f"Mentioned in {len(canonical.source_documents)} documents"},
                    {"name": "Credibility floor", "score": 1.0 - (canonical.credibility_floor - 1) / 4, "weight": 0.4, "description": f"Best source credibility tier: {canonical.credibility_floor}"},
                ],
            },
            "location": {
                "lat": profile.get("location_lat"),
                "lon": profile.get("location_lon"),
                "name": profile.get("location_name"),
            } if profile and profile.get("location_lat") else None,
            "first_seen": canonical.first_seen.isoformat() if canonical.first_seen else None,
            "last_seen": canonical.last_seen.isoformat() if canonical.last_seen else None,
            "document_count": len(canonical.source_documents),
        }
    }


@router.post("/entity/{canonical_id}/generate-bio")
async def generate_entity_bio(canonical_id: str):
    """Trigger bio generation for an entity. Called lazily from the frontend."""
    entity_index = _entity_index
    if entity_index is None:
        return {"error": "Entity index not available"}

    canonical = entity_index.get(canonical_id)
    if canonical is None:
        return {"error": "Entity not found"}

    # Fetch content from source documents
    doc_contents = []
    try:
        async with get_connection() as db:
            doc_ids = canonical.source_documents[:10]
            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                cursor = await db.execute(
                    f"SELECT content FROM documents WHERE id IN ({placeholders})",
                    doc_ids,
                )
                for row in await cursor.fetchall():
                    if row[0]:
                        doc_contents.append(row[0][:500])
    except Exception:
        logger.debug("bio_doc_fetch_failed", exc_info=True)

    if not doc_contents:
        return {"error": "No source documents available for bio generation"}

    # Get anthropic client
    from periphery.config import get_settings
    _settings = get_settings()
    if not _settings.anthropic_api_key:
        return {"error": "Anthropic API key not configured"}

    import anthropic
    client = anthropic.Anthropic(api_key=_settings.anthropic_api_key)

    bio_short, bio_long = await entity_index.generate_bio(
        canonical_id, client, doc_contents
    )

    return {
        "canonical_id": canonical_id,
        "bio_short": bio_short,
        "bio_long": bio_long,
    }


# ── Cluster Detail Endpoint ──────────────────────────────────────────────


@router.get("/cluster/{cluster_id}")
async def get_cluster(cluster_id: str):
    """Return full cluster detail with members, relationships, and trajectories."""
    engine = _get_engine()
    snapshot = engine.snapshot

    if snapshot is None:
        return {"error": "No snapshot available", "cluster": None}

    target = None
    for c in snapshot.clusters:
        if c.cluster_id == cluster_id:
            target = c
            break

    if target is None:
        return {"error": f"Cluster {cluster_id} not found", "cluster": None}

    # Find internal relationships (within cluster)
    internal_rels = target.key_relationships

    # Find external relationships (gradients to other clusters)
    external_rels = [
        {
            "source": g.source_cluster,
            "target": g.target_cluster,
            "score": g.gradient_score,
            "trend": g.gradient_trend,
            "bridge_entities": g.components.bridge_entities,
        }
        for g in snapshot.relational_gradients
        if g.source_cluster == cluster_id or g.target_cluster == cluster_id
    ]

    # Find trajectories
    trajectories = [
        t.model_dump(mode="json")
        for t in snapshot.trajectories
        if t.cluster_id == cluster_id
    ]

    return {
        "cluster": target.model_dump(mode="json"),
        "members": [
            {"document_id": did} for did in target.member_document_ids
        ],
        "internal_relationships": internal_rels,
        "external_relationships": external_rels,
        "trajectories": trajectories,
        "confidence": target.confidence,
        "confidence_explanation": {
            "cross_space_coherence": target.cross_space_coherence,
            "density": target.density,
            "stability": target.stability,
            "status": target.status,
        },
        "rendering": confidence_to_rendering(target.confidence).model_dump(),
    }


# ── Query History Endpoint ───────────────────────────────────────────────


@router.get("/history")
async def get_query_history(
    limit: int = Query(20, ge=1, le=100),
    session_id: str | None = None,
):
    """Return recent query history."""
    engine = _get_engine()
    if engine.query_store is None:
        return {"queries": [], "stats": {}}

    queries = await engine.query_store.get_recent_queries(limit, session_id)
    stats = await engine.query_store.get_query_stats()
    return {"queries": queries, "stats": stats}


# ── Feedback Endpoint ────────────────────────────────────────────────────


@router.post("/feedback/{query_id}")
async def submit_feedback(query_id: str, feedback: FeedbackRequest):
    """Submit analyst feedback on a query result (thumbs up/down, notes)."""
    engine = _get_engine()
    if engine.query_store:
        await engine.query_store.save_feedback(query_id, feedback.model_dump(exclude_none=True))
    return {"status": "ok", "query_id": query_id}


# ── Bookmark Endpoint ────────────────────────────────────────────────────


@router.post("/bookmark")
async def create_bookmark(
    query_id: str,
    session_id: str,
    label: str = "",
):
    """Bookmark a query for persistent monitoring."""
    engine = _get_engine()
    if engine.query_store:
        await engine.query_store.save_bookmark(query_id, session_id, label)
    return {"status": "ok", "query_id": query_id}


@router.get("/bookmarks/{session_id}")
async def get_bookmarks(session_id: str):
    """List bookmarked queries for a session."""
    engine = _get_engine()
    if engine.query_store is None:
        return {"bookmarks": []}
    bookmarks = await engine.query_store.get_bookmarks(session_id)
    return {"bookmarks": bookmarks}


# ── Annotation Endpoint ──────────────────────────────────────────────────


@router.post("/annotate")
async def submit_annotation(body: AnnotationRequest):
    """Submit an analyst annotation (entity merge, relationship confirmation, etc.)."""
    engine = _get_engine()
    if engine.query_store:
        await engine.query_store.save_annotation(
            annotation_type=body.annotation_type,
            target_type=body.target_type,
            target_id=body.target_id,
            annotation_data=body.data,
            session_id=body.session_id,
        )
    return {"status": "ok"}


# ── Streaming Query Endpoint ─────────────────────────────────────────────


@router.websocket("/query/{query_id}/stream")
async def query_stream(websocket: WebSocket, query_id: str):
    """WebSocket endpoint for real-time updates on a subscribed query.

    After an analyst submits a query, the frontend can connect to this
    WebSocket to receive live updates as new data flows in that's
    relevant to the query's scope.
    """
    await websocket.accept()
    engine = _get_engine()

    logger.info("websocket_connected query_id=%s", query_id)

    try:
        while True:
            # Check for updates relevant to this query
            updates = engine.check_updates(query_id)
            for update in updates:
                await websocket.send_json(update)

            # Also check for incoming messages (unsubscribe, ping)
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=5.0
                )
                if data == "unsubscribe":
                    engine.unsubscribe(query_id)
                    await websocket.send_json({"type": "unsubscribed", "query_id": query_id})
                    break
                elif data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # No incoming message — continue polling
                pass

    except WebSocketDisconnect:
        logger.info("websocket_disconnected query_id=%s", query_id)
        engine.unsubscribe(query_id)
    except Exception:
        logger.exception("websocket_error query_id=%s", query_id)
        engine.unsubscribe(query_id)


# ── Legibility Gradient Reference ────────────────────────────────────────


@router.get("/legibility-gradient")
async def get_legibility_gradient():
    """Return the legibility gradient specification for frontend reference."""
    from periphery.query.renderer import LEGIBILITY_GRADIENT
    return {"gradient": LEGIBILITY_GRADIENT}


# ── Entity Backfill Endpoint ─────────────────────────────────────────────


async def backfill_entity_index(entity_index, db_path: str = "") -> int:
    """Backfill canonical entities from existing document_enrichments data.

    Iterates through all enrichment rows, parses entities, and runs them
    through the entity index resolution logic (exact → alias → fuzzy → register).
    """
    from periphery.enrichment.stages.entity_resolution import FUZZY_MATCH_THRESHOLD

    total_processed = 0
    batch_size = 500

    async with get_connection() as db:
        # Count total enrichment rows
        cursor = await db.execute("SELECT COUNT(*) FROM document_enrichments")
        row = await cursor.fetchone()
        total_rows = row[0] if row else 0
        logger.info("backfill_starting total_enrichment_rows=%d", total_rows)

        offset = 0
        while offset < total_rows:
            cursor = await db.execute(
                "SELECT document_id, entities FROM document_enrichments "
                "ORDER BY document_id LIMIT ? OFFSET ?",
                (batch_size, offset),
            )
            rows = await cursor.fetchall()
            if not rows:
                break

            for row in rows:
                doc_id = row[0]
                try:
                    entities_raw = json.loads(row[1]) if row[1] else []
                except (json.JSONDecodeError, TypeError):
                    continue

                if not isinstance(entities_raw, list):
                    continue

                for ent in entities_raw:
                    if not isinstance(ent, dict) or "text" not in ent:
                        continue
                    text = ent["text"].strip()
                    if not text:
                        continue
                    entity_type = ent.get("entity_type", "entity")
                    credibility_tier = ent.get("credibility_tier", 4)

                    # Resolution logic: exact → alias → fuzzy → register
                    canonical = entity_index.lookup_exact(text)
                    if canonical:
                        await entity_index.update(
                            canonical.canonical_id,
                            doc_id=doc_id,
                            credibility_tier=credibility_tier,
                        )
                        continue

                    canonical = entity_index.lookup_alias(text)
                    if canonical:
                        await entity_index.update(
                            canonical.canonical_id,
                            new_alias=text,
                            doc_id=doc_id,
                            credibility_tier=credibility_tier,
                        )
                        continue

                    canonical, score = entity_index.lookup_fuzzy(text, entity_type)
                    if canonical and score >= FUZZY_MATCH_THRESHOLD:
                        await entity_index.update(
                            canonical.canonical_id,
                            new_alias=text,
                            doc_id=doc_id,
                            credibility_tier=credibility_tier,
                        )
                        canonical.merge_confidence = min(canonical.merge_confidence, score)
                        continue

                    await entity_index.register(text, entity_type, doc_id, credibility_tier)
                    total_processed += 1

                # Periodically flush dirty entities
                if entity_index._dirty and len(entity_index._dirty) >= 50:
                    await entity_index.flush()

            offset += batch_size
            logger.info(
                "backfill_progress offset=%d total=%d index_size=%d",
                offset, total_rows, len(entity_index),
            )

    # Final flush
    await entity_index.flush()
    logger.info(
        "backfill_complete new_entities=%d total_index_size=%d",
        total_processed, len(entity_index),
    )
    return len(entity_index)


@router.post("/admin/backfill-entities")
async def admin_backfill_entities(x_admin_key: str | None = Header(None)):
    """Trigger entity backfill from existing document_enrichments data. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    entity_index = _entity_index
    if entity_index is None:
        return {"error": "Entity index not available"}

    count = await backfill_entity_index(entity_index)
    return {
        "status": "ok",
        "total_canonical_entities": count,
        "message": f"Backfill complete. {count} canonical entities in index.",
    }


# ── Personal Ontology Endpoints ────────────────────────────────────────────

from periphery.auth.middleware import get_current_user
from periphery.auth.models import (
    AuthenticatedUser,
    CreateGroupRequest,
    CreateViewRequest,
    UpdateGroupRequest,
)
from periphery.auth.personal import (
    create_group,
    create_view,
    delete_group,
    delete_view,
    get_personal_overlay,
    list_groups,
    list_views,
    remove_annotation,
    set_annotation,
    update_group,
)


@router.post("/personal/pin/{canonical_id}")
async def pin_entity(
    canonical_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Pin an entity to your personal ontology."""
    await set_annotation(user.user_id, canonical_id, "pin")
    return {"status": "ok", "canonical_id": canonical_id, "pinned": True}


@router.delete("/personal/pin/{canonical_id}")
async def unpin_entity(
    canonical_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Unpin an entity from your personal ontology."""
    await remove_annotation(user.user_id, canonical_id, "pin")
    return {"status": "ok", "canonical_id": canonical_id, "pinned": False}


@router.post("/personal/hide/{canonical_id}")
async def hide_entity(
    canonical_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Hide an entity from your personal ontology view."""
    await set_annotation(user.user_id, canonical_id, "hide")
    return {"status": "ok", "canonical_id": canonical_id, "hidden": True}


@router.delete("/personal/hide/{canonical_id}")
async def unhide_entity(
    canonical_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Unhide an entity in your personal ontology view."""
    await remove_annotation(user.user_id, canonical_id, "hide")
    return {"status": "ok", "canonical_id": canonical_id, "hidden": False}


@router.post("/personal/annotate/{canonical_id}")
async def annotate_entity(
    canonical_id: str,
    body: dict[str, Any],
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Add a note or tag to an entity."""
    ann_type = body.get("type", "note")
    if ann_type not in ("note", "tag"):
        ann_type = "note"
    await set_annotation(user.user_id, canonical_id, ann_type, body.get("data", {}))
    return {"status": "ok", "canonical_id": canonical_id}


@router.post("/personal/groups")
async def create_entity_group(
    body: CreateGroupRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a custom entity group."""
    group = await create_group(
        user.user_id, body.name, body.description, body.entity_ids
    )
    return group.model_dump(mode="json")


@router.put("/personal/groups/{group_id}")
async def update_entity_group(
    group_id: str,
    body: UpdateGroupRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update a custom entity group."""
    group = await update_group(
        user.user_id, group_id, body.name, body.description, body.entity_ids
    )
    if not group:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Group not found")
    return group.model_dump(mode="json")


@router.delete("/personal/groups/{group_id}")
async def delete_entity_group(
    group_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a custom entity group."""
    deleted = await delete_group(user.user_id, group_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "ok", "group_id": group_id}


@router.get("/personal/groups")
async def get_entity_groups(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List your custom entity groups."""
    groups = await list_groups(user.user_id)
    return [g.model_dump(mode="json") for g in groups]


@router.get("/personal/views")
async def get_saved_views(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List your saved ontology views."""
    views = await list_views(user.user_id)
    return [v.model_dump(mode="json") for v in views]


@router.post("/personal/views")
async def create_saved_view(
    body: CreateViewRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a saved ontology view."""
    view = await create_view(user.user_id, body.name, body.filters, body.layout)
    return view.model_dump(mode="json")


@router.delete("/personal/views/{view_id}")
async def delete_saved_view(
    view_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a saved ontology view."""
    deleted = await delete_view(user.user_id, view_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="View not found")
    return {"status": "ok", "view_id": view_id}


@router.get("/personal/overlay")
async def get_overlay(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the full personal ontology overlay for the current user."""
    overlay = await get_personal_overlay(user.user_id)
    return overlay.model_dump(mode="json")
