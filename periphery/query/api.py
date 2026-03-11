"""Component 6 — API Endpoints for the Query Interface.

Exposes the full analytical query pipeline, ontology snapshot, entity
detail, cluster detail, query history, streaming, and analyst annotations
through a FastAPI router.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
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

# Module-level cache for enrichment data, keyed by snapshot_id
_enrichment_cache: dict[str, dict[str, Any]] = {}


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

    # Build deduplicated entity list — key by lowercase text, keep highest confidence
    best_entity: dict[str, dict] = {}  # lowercase name -> best entity dict
    entity_doc_count: dict[str, int] = {}  # lowercase name -> document count
    entity_cluster_ids: dict[str, set] = {}  # lowercase name -> set of cluster IDs

    # Build reverse map: doc_id -> list of cluster_ids
    doc_to_clusters: dict[str, list[str]] = {}
    for cluster_id, doc_ids in cluster_doc_map.items():
        for did in doc_ids:
            doc_to_clusters.setdefault(did, []).append(cluster_id)

    for did, ents in doc_entities.items():
        for ent in ents:
            if not isinstance(ent, dict) or "text" not in ent:
                continue
            key = ent["text"].strip().lower()
            if not key:
                continue

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
                best_entity[key] = ent

    # Build final entity list
    entities: list[dict[str, Any]] = []
    # Also build a lookup: canonical_id -> True for relationship filtering
    entity_canonical_ids: set[str] = set()

    for key, ent in best_entity.items():
        canonical_id = ent.get("canonical_id", ent["text"])
        entity_canonical_ids.add(canonical_id)

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

        entity_conf = ent.get("confidence", 0.5)
        rendering = confidence_to_rendering(entity_conf).model_dump() if include_rendering else {}

        entities.append({
            "canonical_id": canonical_id,
            "name": ent["text"],
            "entity_type": ent.get("entity_type", "entity"),
            "aliases": [],
            "confidence": entity_conf,
            "source_count": entity_doc_count.get(key, 1),
            "cluster_ids": sorted(entity_cluster_ids.get(key, set())),
            "first_seen": generated_at_iso,
            "last_seen": generated_at_iso,
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

    # Build entity and relationship lists from enrichment data
    enrichment_available = False
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    try:
        # Collect all member document IDs and build cluster->docs map
        all_member_doc_ids: list[str] = []
        cluster_doc_map: dict[str, list[str]] = {}
        seen_doc_ids: set[str] = set()
        for c in clusters:
            cluster_doc_map[c.cluster_id] = c.member_document_ids
            for did in c.member_document_ids:
                if did not in seen_doc_ids:
                    seen_doc_ids.add(did)
                    all_member_doc_ids.append(did)

        if all_member_doc_ids:
            entities, relationships = await _load_enrichment_entities(
                member_doc_ids=all_member_doc_ids,
                cluster_doc_map=cluster_doc_map,
                snapshot_id=snapshot.snapshot_id,
                generated_at_iso=snapshot.generated_at.isoformat(),
                include_rendering=include_rendering,
            )
            enrichment_available = True
            logger.info(
                "snapshot_enrichment_loaded entities=%d relationships=%d",
                len(entities),
                len(relationships),
            )
    except Exception:
        logger.warning(
            "snapshot_enrichment_fallback: could not load enrichment data, "
            "falling back to cluster key_entities",
            exc_info=True,
        )

    # Fallback: build entities from cluster key_entities if enrichment unavailable
    if not enrichment_available:
        seen_entities: set[str] = set()
        for c in clusters:
            for entity_name in c.key_entities:
                if entity_name not in seen_entities:
                    seen_entities.add(entity_name)
                    entity_confidence = c.confidence
                    rendering = confidence_to_rendering(entity_confidence).model_dump() if include_rendering else {}
                    entities.append({
                        "canonical_id": entity_name,
                        "name": entity_name,
                        "entity_type": "entity",
                        "aliases": [],
                        "confidence": entity_confidence,
                        "source_count": len(c.member_document_ids),
                        "cluster_ids": [c.cluster_id],
                        "first_seen": snapshot.generated_at.isoformat(),
                        "last_seen": snapshot.generated_at.isoformat(),
                        "rendering": rendering,
                    })

        for c in clusters:
            for i, rel in enumerate(c.key_relationships):
                if isinstance(rel, dict):
                    relationships.append({
                        "id": f"rel-{c.cluster_id}-{i}",
                        "subject_id": rel.get("subject", rel.get("source", "")),
                        "predicate": rel.get("predicate", rel.get("type", "related_to")),
                        "object_id": rel.get("object", rel.get("target", "")),
                        "confidence": rel.get("confidence", c.confidence),
                        "evidence_sentences": [],
                        "temporal_context": "current",
                        "extraction_tier": "co_occurrence",
                        "source_count": 1,
                        "first_seen": snapshot.generated_at.isoformat(),
                        "last_seen": snapshot.generated_at.isoformat(),
                    })
                elif isinstance(rel, str):
                    relationships.append({
                        "id": f"rel-{c.cluster_id}-{i}",
                        "subject_id": c.cluster_id,
                        "predicate": rel,
                        "object_id": "",
                        "confidence": c.confidence,
                        "evidence_sentences": [],
                        "temporal_context": "current",
                        "extraction_tier": "co_occurrence",
                        "source_count": 1,
                        "first_seen": snapshot.generated_at.isoformat(),
                        "last_seen": snapshot.generated_at.isoformat(),
                    })

    result: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "timestamp": snapshot.generated_at.isoformat(),
        "corpus_stats": snapshot.corpus_stats.model_dump(),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "cluster_count": len(clusters),
        "entities": entities,
        "relationships": relationships,
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


# ── Entity Detail Endpoint ───────────────────────────────────────────────


@router.get("/entity/{canonical_id}")
async def get_entity(canonical_id: str):
    """Return full entity detail with relationships and cluster memberships."""
    engine = _get_engine()
    snapshot = engine.snapshot

    if snapshot is None:
        return {"error": "No snapshot available", "entity": None}

    # Search for entity across clusters
    cluster_memberships = []
    relationships = []
    source_documents: set[str] = set()

    for cluster in snapshot.clusters:
        for entity_name in cluster.key_entities:
            if canonical_id in entity_name or entity_name in canonical_id:
                cluster_memberships.append({
                    "cluster_id": cluster.cluster_id,
                    "label": cluster.label,
                    "confidence": cluster.confidence,
                    "size": cluster.size,
                })
                source_documents.update(cluster.member_document_ids[:10])
                relationships.extend(cluster.key_relationships[:5])
                break

    entity_data = {
        "canonical_id": canonical_id,
        "cluster_memberships": cluster_memberships,
        "relationships": relationships,
        "source_documents": list(source_documents)[:50],
        "rendering": confidence_to_rendering(
            max((cm["confidence"] for cm in cluster_memberships), default=0.0)
        ).model_dump(),
    }

    return {"entity": entity_data}


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
async def submit_feedback(query_id: str, feedback: dict[str, Any]):
    """Submit analyst feedback on a query result (thumbs up/down, notes)."""
    engine = _get_engine()
    if engine.query_store:
        await engine.query_store.save_feedback(query_id, feedback)
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
async def submit_annotation(body: dict[str, Any]):
    """Submit an analyst annotation (entity merge, relationship confirmation, etc.).

    Body format:
    {
        "annotation_type": "entity_merge" | "relationship_confirm" | "relationship_deny",
        "target_type": "entity" | "relationship" | "cluster",
        "target_id": str,
        "data": dict,
        "session_id": str
    }
    """
    engine = _get_engine()
    if engine.query_store:
        await engine.query_store.save_annotation(
            annotation_type=body.get("annotation_type", ""),
            target_type=body.get("target_type", ""),
            target_id=body.get("target_id", ""),
            annotation_data=body.get("data", {}),
            session_id=body.get("session_id", ""),
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
