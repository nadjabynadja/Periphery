"""Component 3 — Multi-Space Retriever.

Executes query plan operations against FAISS embedding spaces and the
ontology snapshot. Supports parallel execution of independent operations
and result fusion via ranked, union, or intersection strategies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np

from periphery.crystallizer.models import LivingOntologySnapshot
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager
from periphery.query.models import (
    AnomalyResult,
    ClusterResult,
    EmergingStructureResult,
    EntityResult,
    PlanOperation,
    QueryPlan,
    RelationalPath,
    RelationshipResult,
    RetrievalResults,
    TrajectoryResult,
)

logger = logging.getLogger(__name__)


class MultiSpaceRetriever:
    """Executes query plans against embedding spaces and the ontology snapshot."""

    def __init__(
        self,
        faiss_store: FAISSStore,
        multi_space: MultiSpaceIndexManager | None = None,
        entity_index: Any | None = None,
        db_path: str = "",
    ) -> None:
        self._store = faiss_store
        self._multi_space = multi_space
        self._entity_index = entity_index
        self._db_path = db_path

    async def execute(
        self,
        plan: QueryPlan,
        query_text: str,
        snapshot: LivingOntologySnapshot | None,
        confidence_floor: float = 0.0,
        max_results: int = 50,
    ) -> tuple[RetrievalResults, int]:
        """Execute a query plan and return merged results.

        Returns (results, elapsed_ms).
        """
        start = time.monotonic()

        # Group operations by dependency level for parallel execution
        completed: dict[str, Any] = {}
        remaining = list(plan.operations)

        while remaining:
            # Find operations whose dependencies are all satisfied
            ready = [
                op for op in remaining
                if all(dep in completed for dep in op.depends_on)
            ]
            if not ready:
                # Deadlock — break by running all remaining
                ready = remaining

            # Execute ready operations in parallel
            tasks = [
                self._execute_operation(op, query_text, snapshot, completed)
                for op in ready
            ]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            for op, result in zip(ready, results_list):
                if isinstance(result, Exception):
                    logger.error("operation_failed op=%s: %s", op.operation_id, result)
                    completed[op.operation_id] = {}
                else:
                    completed[op.operation_id] = result

            remaining = [op for op in remaining if op not in ready]

        # Merge all operation results
        merged = self._merge_results(
            plan.query_id, completed, plan.merge_strategy,
            confidence_floor, max_results,
        )

        elapsed = int((time.monotonic() - start) * 1000)
        return merged, elapsed

    async def _execute_operation(
        self,
        op: PlanOperation,
        query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single retrieval operation."""
        handlers = {
            "entity_search": self._op_entity_search,
            "cluster_retrieval": self._op_cluster_retrieval,
            "semantic_search": self._op_semantic_search,
            "relational_path": self._op_relational_path,
            "geographic_filter": self._op_geographic_filter,
            "temporal_filter": self._op_temporal_filter,
            "trajectory_retrieval": self._op_trajectory_retrieval,
            "anomaly_retrieval": self._op_anomaly_retrieval,
        }

        handler = handlers.get(op.type)
        if handler is None:
            logger.warning("unknown_operation_type: %s", op.type)
            return {}

        return await handler(op, query_text, snapshot, completed)

    async def _op_entity_search(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Search for entities by name using the entity index and FAISS."""
        entity_names = op.parameters.get("entity_names", [])
        entity_types = op.parameters.get("entity_types", [])
        entities: list[EntityResult] = []

        # Search the entity resolution index
        if self._entity_index is not None:
            for name in entity_names:
                canonical = self._entity_index.lookup_exact(name)
                if canonical is None:
                    canonical = self._entity_index.lookup_alias(name)
                if canonical is None and entity_types:
                    for etype in entity_types:
                        canonical, _ = self._entity_index.lookup_fuzzy(name, etype)
                        if canonical:
                            break
                if canonical is None:
                    canonical, _ = self._entity_index.lookup_fuzzy(name, "")

                if canonical:
                    # Find cluster memberships from snapshot
                    cluster_memberships = []
                    if snapshot:
                        for c in snapshot.clusters:
                            if canonical.canonical_name in c.key_entities:
                                cluster_memberships.append(c.cluster_id)

                    entities.append(EntityResult(
                        canonical_id=canonical.canonical_id,
                        name=canonical.canonical_name,
                        type=canonical.entity_type,
                        confidence=canonical.merge_confidence,
                        cluster_memberships=cluster_memberships,
                        source_documents=canonical.source_documents[:20],
                        relevance_score=1.0,
                    ))

        # Also search entity FAISS space for approximate matches
        if self._multi_space and entity_names:
            search_text = " ".join(entity_names)
            try:
                vec = embedder.embed([search_text])[0]
                faiss_results = self._multi_space.search("entity", vec, top_k=20)
                for doc_id, score in faiss_results:
                    if score > 0.3:
                        entities.append(EntityResult(
                            canonical_id=doc_id,
                            name=doc_id,
                            type="document_ref",
                            confidence=float(score),
                            relevance_score=float(score),
                            source_documents=[doc_id],
                        ))
            except Exception:
                logger.debug("entity_faiss_search_failed", exc_info=True)

        return {"entities": entities}

    async def _op_cluster_retrieval(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve full cluster data from the ontology snapshot."""
        cluster_ids = set(op.parameters.get("cluster_ids", []))
        clusters: list[ClusterResult] = []

        if snapshot is None:
            return {"clusters": clusters}

        for c in snapshot.clusters:
            if cluster_ids and c.cluster_id not in cluster_ids:
                continue

            # Find trajectories for this cluster
            cluster_trajs = [
                {"trajectory_id": t.trajectory_id, "pattern": t.pattern,
                 "velocity": t.velocity, "confidence": t.confidence}
                for t in snapshot.trajectories
                if t.cluster_id == c.cluster_id
            ]

            clusters.append(ClusterResult(
                cluster_id=c.cluster_id,
                label=c.label,
                confidence=c.confidence,
                size=c.size,
                key_entities=[{"name": e} for e in c.key_entities],
                key_relationships=c.key_relationships,
                trajectories=cluster_trajs,
                geographic_center=c.geographic_center,
                temporal_center=c.temporal_center.isoformat() if c.temporal_center else None,
                relevance_score=1.0 if cluster_ids else 0.5,
            ))

        return {"clusters": clusters}

    async def _op_semantic_search(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Embed query and search semantic FAISS index."""
        search_text = op.parameters.get("subquery_text", query_text)
        top_k = op.parameters.get("top_k", 50)

        try:
            vec = embedder.embed([search_text])[0]
        except Exception:
            logger.error("embedding_failed", exc_info=True)
            return {"doc_ids": [], "scores": {}}

        # Search semantic space
        results = []
        if self._multi_space:
            results = self._multi_space.search("semantic", vec, top_k=top_k)
        if not results:
            results = self._store.search(vec, top_k=top_k)

        doc_ids = [r[0] for r in results]
        scores = {r[0]: float(r[1]) for r in results}

        return {"doc_ids": doc_ids, "scores": scores}

    async def _op_relational_path(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Search for relational paths between two entities."""
        entity_a = op.parameters.get("entity_a", "")
        entity_b = op.parameters.get("entity_b", "")
        max_hops = op.parameters.get("max_hops", 4)
        paths: list[RelationalPath] = []

        if not snapshot or not entity_a or not entity_b:
            return {"relational_paths": paths}

        # Check for direct relationships in clusters
        # Build an adjacency from cluster key_relationships
        a_clusters = set()
        b_clusters = set()
        for c in snapshot.clusters:
            if entity_a.lower() in [e.lower() for e in c.key_entities]:
                a_clusters.add(c.cluster_id)
            if entity_b.lower() in [e.lower() for e in c.key_entities]:
                b_clusters.add(c.cluster_id)

        # Direct: both in same cluster
        shared = a_clusters & b_clusters
        if shared:
            for cid in shared:
                paths.append(RelationalPath(
                    from_entity=entity_a,
                    to_entity=entity_b,
                    path=[{"cluster_id": cid, "type": "co_membership"}],
                    path_confidence=0.8,
                    path_type="direct",
                ))

        # Cluster-mediated: connected via relational gradients
        if not paths:
            for grad in snapshot.relational_gradients:
                source_match = grad.source_cluster in a_clusters and grad.target_cluster in b_clusters
                target_match = grad.target_cluster in a_clusters and grad.source_cluster in b_clusters
                if source_match or target_match:
                    paths.append(RelationalPath(
                        from_entity=entity_a,
                        to_entity=entity_b,
                        path=[{
                            "source_cluster": grad.source_cluster,
                            "target_cluster": grad.target_cluster,
                            "gradient_score": grad.gradient_score,
                            "bridge_entities": grad.components.bridge_entities,
                        }],
                        path_confidence=grad.gradient_score,
                        path_type="cluster_mediated",
                    ))

        return {"relational_paths": paths}

    async def _op_geographic_filter(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Filter clusters/entities by geographic scope."""
        regions = [r.lower() for r in op.parameters.get("regions", [])]
        coordinates = op.parameters.get("coordinates")
        clusters: list[ClusterResult] = []

        if not snapshot:
            return {"clusters": clusters}

        for c in snapshot.clusters:
            match = False
            if regions:
                # Check cluster label and key entities for region mentions
                label_lower = (c.label or "").lower()
                entities_lower = " ".join(c.key_entities).lower()
                for region in regions:
                    if region in label_lower or region in entities_lower:
                        match = True
                        break

            if coordinates and c.geographic_center:
                # Simple distance check
                import math
                lat1, lon1 = coordinates.get("lat", 0), coordinates.get("lon", 0)
                lat2 = c.geographic_center.get("lat", 0)
                lon2 = c.geographic_center.get("lon", 0)
                radius_km = coordinates.get("radius_km", 500)
                # Haversine approximation
                dlat = math.radians(lat2 - lat1)
                dlon = math.radians(lon2 - lon1)
                a = (math.sin(dlat / 2) ** 2 +
                     math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
                     math.sin(dlon / 2) ** 2)
                dist_km = 6371 * 2 * math.asin(math.sqrt(a))
                if dist_km <= radius_km:
                    match = True

            if match:
                clusters.append(ClusterResult(
                    cluster_id=c.cluster_id,
                    label=c.label,
                    confidence=c.confidence,
                    size=c.size,
                    key_entities=[{"name": e} for e in c.key_entities],
                    key_relationships=c.key_relationships,
                    geographic_center=c.geographic_center,
                    relevance_score=0.8,
                ))

        return {"clusters": clusters}

    async def _op_temporal_filter(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Filter by temporal scope."""
        # Temporal filtering would use the temporal FAISS index
        # For now, return all clusters with temporal center data
        clusters: list[ClusterResult] = []
        if not snapshot:
            return {"clusters": clusters}

        for c in snapshot.clusters:
            if c.temporal_center is not None:
                clusters.append(ClusterResult(
                    cluster_id=c.cluster_id,
                    label=c.label,
                    confidence=c.confidence,
                    size=c.size,
                    key_entities=[{"name": e} for e in c.key_entities],
                    temporal_center=c.temporal_center.isoformat(),
                    relevance_score=0.6,
                ))

        return {"clusters": clusters}

    async def _op_trajectory_retrieval(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve trajectories matching the query scope."""
        trajectories: list[TrajectoryResult] = []
        if not snapshot:
            return {"trajectories": trajectories}

        cluster_ids = set(op.parameters.get("cluster_ids", []))
        entity_names = [n.lower() for n in op.parameters.get("entity_names", [])]

        # Build cluster-to-label map
        cluster_labels = {c.cluster_id: c.label for c in snapshot.clusters}
        cluster_entities = {
            c.cluster_id: [e.lower() for e in c.key_entities]
            for c in snapshot.clusters
        }

        for t in snapshot.trajectories:
            match = False
            if cluster_ids and t.cluster_id in cluster_ids:
                match = True
            elif entity_names:
                ents = cluster_entities.get(t.cluster_id, [])
                for name in entity_names:
                    if any(name in e for e in ents):
                        match = True
                        break
            elif not cluster_ids and not entity_names:
                match = True

            if match:
                description = (
                    f"Cluster '{cluster_labels.get(t.cluster_id, t.cluster_id)}' "
                    f"shows {t.pattern} pattern with velocity {t.velocity:.3f}"
                )
                extrapolation = None
                if op.parameters.get("include_extrapolation") and t.pattern != "stable":
                    extrapolation = {
                        "note": "Trajectory extrapolation — speculative",
                        "pattern": t.pattern,
                        "projected_velocity": t.velocity * 1.1,
                        "confidence_decay": 0.8,
                    }

                trajectories.append(TrajectoryResult(
                    trajectory_id=t.trajectory_id,
                    cluster_label=cluster_labels.get(t.cluster_id, ""),
                    pattern=t.pattern,
                    velocity=t.velocity,
                    confidence=t.confidence,
                    description=description,
                    extrapolation=extrapolation,
                ))

        return {"trajectories": trajectories}

    async def _op_anomaly_retrieval(
        self, op: PlanOperation, query_text: str,
        snapshot: LivingOntologySnapshot | None,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve anomalies matching the query scope."""
        anomalies: list[AnomalyResult] = []
        if not snapshot:
            return {"anomalies": anomalies}

        for a in snapshot.anomalies:
            if a.resolved:
                continue

            anomalies.append(AnomalyResult(
                anomaly_id=a.anomaly_id,
                type=a.anomaly_type,
                score=a.anomaly_score,
                description=(
                    f"{a.anomaly_type} anomaly (score={a.anomaly_score:.2f}) "
                    f"in spaces {a.outlier_spaces}, nearest cluster: {a.nearest_cluster}"
                ),
                related_entities=[a.document_id],
                source_credibility=a.source_credibility,
            ))

        # Sort by score descending, then credibility ascending (1 = best)
        anomalies.sort(key=lambda x: (-x.score, x.source_credibility))
        return {"anomalies": anomalies}

    def _merge_results(
        self,
        query_id: str,
        completed: dict[str, Any],
        merge_strategy: str,
        confidence_floor: float,
        max_results: int,
    ) -> RetrievalResults:
        """Merge results from all completed operations."""
        all_entities: list[EntityResult] = []
        all_clusters: list[ClusterResult] = []
        all_relationships: list[RelationshipResult] = []
        all_trajectories: list[TrajectoryResult] = []
        all_anomalies: list[AnomalyResult] = []
        all_paths: list[RelationalPath] = []
        all_emerging: list[EmergingStructureResult] = []

        for op_id, result in completed.items():
            if not isinstance(result, dict):
                continue
            all_entities.extend(result.get("entities", []))
            all_clusters.extend(result.get("clusters", []))
            all_relationships.extend(result.get("relationships", []))
            all_trajectories.extend(result.get("trajectories", []))
            all_anomalies.extend(result.get("anomalies", []))
            all_paths.extend(result.get("relational_paths", []))
            all_emerging.extend(result.get("emerging_structures", []))

        # Deduplicate clusters by cluster_id, keeping highest relevance
        seen_clusters: dict[str, ClusterResult] = {}
        for c in all_clusters:
            existing = seen_clusters.get(c.cluster_id)
            if existing is None or c.relevance_score > existing.relevance_score:
                seen_clusters[c.cluster_id] = c
        all_clusters = list(seen_clusters.values())

        # Deduplicate entities by canonical_id
        seen_entities: dict[str, EntityResult] = {}
        for e in all_entities:
            existing = seen_entities.get(e.canonical_id)
            if existing is None or e.relevance_score > existing.relevance_score:
                seen_entities[e.canonical_id] = e
        all_entities = list(seen_entities.values())

        # Apply confidence floor
        if confidence_floor > 0:
            all_entities = [e for e in all_entities if e.confidence >= confidence_floor]
            all_clusters = [c for c in all_clusters if c.confidence >= confidence_floor]
            all_relationships = [r for r in all_relationships if r.confidence >= confidence_floor]

        # Sort by relevance
        all_entities.sort(key=lambda x: -x.relevance_score)
        all_clusters.sort(key=lambda x: -x.relevance_score)
        all_trajectories.sort(key=lambda x: -x.confidence)
        all_anomalies.sort(key=lambda x: -x.score)

        # Apply max_results limit
        return RetrievalResults(
            query_id=query_id,
            entities=all_entities[:max_results],
            clusters=all_clusters[:max_results],
            relationships=all_relationships[:max_results],
            trajectories=all_trajectories[:max_results],
            anomalies=all_anomalies[:max_results],
            relational_paths=all_paths[:max_results],
            emerging_structures=all_emerging[:max_results],
        )
