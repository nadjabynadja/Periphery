"""Crystallizer worker — the core analytical engine.

Runs as a continuous background process performing multi-space clustering,
trajectory detection, relational gradient analysis, and anomaly detection.
Produces living ontology snapshots that represent the current state of
emergent structure in the embedding spaces.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import structlog

from periphery.crystallizer.anomalies import AnomalyDetector
from periphery.crystallizer.clustering import MultiSpaceClusterEngine, run_clustering
from periphery.crystallizer.gradients import GradientAnalyzer
from periphery.crystallizer.graph import OntologyGraph
from periphery.crystallizer.labeler import (
    extract_key_entities,
    extract_key_relationships,
    generate_label,
)
from periphery.crystallizer.models import (
    Anomaly,
    ClusterCorrelation,
    ConvergenceAlert,
    CorpusStats,
    DetectedCluster,
    EmergingStructure,
    LivingOntologySnapshot,
    RelationalGradient,
    Trajectory,
)
from periphery.crystallizer.persistence import CrystallizerStore
from periphery.crystallizer.trajectories import TrajectoryDetector
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager
from periphery.models import Cluster, Document

logger = structlog.get_logger(__name__)


class CrystallizerWorker:
    """Background worker that continuously analyzes the embedding space.

    Orchestrates four analytical sub-processes:
      1. Cluster Detection — HDBSCAN over each embedding space
      2. Trajectory Detection — centroid tracking and pattern classification
      3. Relational Gradient Analysis — emergent inter-cluster relationships
      4. Anomaly Detection — outlier scoring and classification

    Scheduling:
      - Full reclustering: every N new documents or M seconds
      - Incremental updates: between full runs via approximate_predict
      - Trajectory/gradient updates: after full reclustering
      - Anomaly scoring: on every run
    """

    def __init__(
        self,
        store: FAISSStore,
        documents: dict[str, Document],
        interval: int = 300,
        *,
        multi_space_manager: MultiSpaceIndexManager | None = None,
        db_path: str = "",
        full_recluster_interval_docs: int = 100,
        full_recluster_interval_seconds: int = 3600,
        incremental_update_interval_seconds: int = 60,
        min_cluster_size: int = 5,
        min_samples: int = 3,
        cluster_selection_epsilon: float = 0.0,
        trajectory_min_snapshots: int = 5,
        auto_label_with_llm: bool = False,
        anthropic_api_key: str = "",
    ):
        self.store = store
        self.documents = documents
        self.interval = interval
        self._multi_space = multi_space_manager

        # Legacy graph for backward compatibility
        self.graph = OntologyGraph()
        self.clusters: list[Cluster] = []
        self.labels: np.ndarray | None = None
        self.stats: dict = {}
        self.last_run: datetime | None = None

        # Core engines
        self._cluster_engine = MultiSpaceClusterEngine(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            cluster_selection_epsilon=cluster_selection_epsilon,
        )
        self._trajectory_detector = TrajectoryDetector(
            min_snapshots=trajectory_min_snapshots,
        )
        self._gradient_analyzer = GradientAnalyzer()
        self._anomaly_detector = AnomalyDetector()

        # Persistence
        self._store_db: CrystallizerStore | None = None
        if db_path:
            self._store_db = CrystallizerStore(db_path)

        # Living snapshot (in-memory for fast query access)
        self._current_snapshot: LivingOntologySnapshot | None = None

        # Scheduling state
        self._full_recluster_interval_docs = full_recluster_interval_docs
        self._full_recluster_interval_seconds = full_recluster_interval_seconds
        self._incremental_interval = incremental_update_interval_seconds
        self._docs_since_last_full = 0
        self._last_full_recluster: datetime | None = None

        # Cluster lifecycle
        self._previous_cluster_members: dict[str, set[str]] = {}

        # Auto-labeling
        self._auto_label_llm = auto_label_with_llm
        self._anthropic_api_key = anthropic_api_key

        # Background task management
        self._task: asyncio.Task | None = None
        self._running = False

        # Callback for critic scoring
        self.on_crystallize: Callable | None = None

    @property
    def current_snapshot(self) -> LivingOntologySnapshot | None:
        return self._current_snapshot

    @property
    def cluster_engine(self) -> MultiSpaceClusterEngine:
        return self._cluster_engine

    @property
    def trajectory_detector(self) -> TrajectoryDetector:
        return self._trajectory_detector

    @property
    def anomaly_detector(self) -> AnomalyDetector:
        return self._anomaly_detector

    @property
    def gradient_analyzer(self) -> GradientAnalyzer:
        return self._gradient_analyzer

    async def start(self) -> None:
        """Start the background crystallization loop."""
        if self._store_db:
            await self._store_db.initialize()
            # Try to load previous snapshot
            self._current_snapshot = await self._store_db.load_latest_snapshot()

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("crystallizer_worker_started", interval=self.interval)

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("crystallizer_worker_stopped")

    async def _loop(self) -> None:
        """Main loop — run crystallization on schedule."""
        while self._running:
            try:
                await self.crystallize()
            except Exception:
                logger.exception("crystallizer_run_failed")
            await asyncio.sleep(self.interval)

    async def crystallize(self) -> dict:
        """Run one full crystallization pass.

        Performs multi-space clustering, trajectory detection, relational
        gradient analysis, anomaly detection, and assembles the living
        ontology snapshot.
        """
        start_time = time.monotonic()

        # Determine if we should do full reclustering or incremental
        should_full = self._should_full_recluster()

        if self._multi_space is not None:
            result = await self._crystallize_multi_space(full_recluster=should_full)
        else:
            result = await self._crystallize_legacy()

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        result["processing_time_ms"] = elapsed_ms
        self.last_run = datetime.now(timezone.utc)
        self.stats = result

        logger.info(
            "crystallization_complete",
            mode="full" if should_full else "incremental",
            elapsed_ms=elapsed_ms,
            clusters=result.get("n_clusters", 0),
            anomalies=result.get("n_anomalies", 0),
        )

        return result

    async def notify_new_documents(self, count: int) -> None:
        """Called when new documents are embedded, to track recluster scheduling."""
        self._docs_since_last_full += count

    async def _crystallize_multi_space(self, full_recluster: bool = True) -> dict:
        """Full multi-space crystallization pipeline."""
        # Phase 1: Gather vectors from all spaces
        spaces = self._multi_space.spaces
        space_vectors: dict[str, np.ndarray] = {}
        space_doc_ids: dict[str, list[str]] = {}

        for space in spaces:
            vectors = self._multi_space.get_all_vectors(space)
            doc_ids = self._multi_space.get_all_ids(space)
            if vectors.shape[0] > 0:
                space_vectors[space] = vectors
                space_doc_ids[space] = doc_ids

        if not space_vectors:
            return {"status": "skipped", "reason": "no_vectors"}

        # Phase 2: Cluster detection
        if full_recluster:
            cluster_stats = self._cluster_engine.cluster_all_spaces(
                space_vectors, space_doc_ids
            )
            self._last_full_recluster = datetime.now(timezone.utc)
            self._docs_since_last_full = 0
        else:
            # Incremental: predict new points against existing clusters
            incremental_results = self._cluster_engine.predict_incremental(space_vectors)
            for space, (labels, strengths) in incremental_results.items():
                clusterer = self._cluster_engine.clusterers.get(space)
                if clusterer is not None:
                    clusterer._labels = labels
            cluster_stats = {"mode": "incremental"}
        # Phase 3: Cross-space correlation
        correlations = self._cluster_engine.correlate_clusters(space_doc_ids)

        # Phase 4: Build enrichment context for gradient analysis
        doc_entities, doc_relationships, doc_metadata = await self._load_enrichment_context(
            space_doc_ids
        )

        # Phase 5: Build detected clusters
        try:
            detected_clusters = self._build_detected_clusters(
                correlations, space_vectors, space_doc_ids,
                doc_entities, doc_relationships,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"BUILD DETECTED CLUSTERS FAILED: {type(e).__name__}: {e}", flush=True)
            raise

        # Phase 6: Cluster lifecycle tracking
        self._track_cluster_lifecycle(detected_clusters)

        # Phase 7: Trajectory detection (only after full reclustering)
        trajectories: list[Trajectory] = []
        convergence_alerts: list[ConvergenceAlert] = []
        if full_recluster:
            all_centroids = self._cluster_engine.get_all_centroids(space_vectors)
            for space, centroids in all_centroids.items():
                self._trajectory_detector.record_centroids(space, centroids)

            trajectories = self._trajectory_detector.detect_trajectories()
            convergence_alerts = self._trajectory_detector.detect_convergences()

            # Mark convergence patterns on trajectories
            for alert in convergence_alerts:
                for traj in trajectories:
                    if traj.cluster_id == alert.cluster_a:
                        traj.pattern = "convergence"
                        traj.converging_with = alert.cluster_b
                    elif traj.cluster_id == alert.cluster_b:
                        traj.pattern = "convergence"
                        traj.converging_with = alert.cluster_a

        # Phase 8: Relational gradient analysis (only after full reclustering)
        gradients: list[RelationalGradient] = []
        if full_recluster and detected_clusters:
            cluster_member_map = {
                c.cluster_id: set(c.member_document_ids) for c in detected_clusters
            }
            cluster_temporal = {
                c.cluster_id: c.temporal_center.timestamp() if c.temporal_center else None
                for c in detected_clusters
            }
            cluster_geo = {
                c.cluster_id: (c.geographic_center["lat"], c.geographic_center["lon"])
                if c.geographic_center else None
                for c in detected_clusters
            }

            gradients = self._gradient_analyzer.compute_gradients(
                cluster_member_map, doc_entities, doc_relationships,
                cluster_temporal, cluster_geo,
            )

        # Phase 9: Anomaly detection
        noise_doc_ids = self._cluster_engine.get_all_noise_doc_ids(space_doc_ids)
        all_centroids = self._cluster_engine.get_all_centroids(space_vectors)
        anomalies = self._anomaly_detector.detect(
            noise_doc_ids, space_vectors, space_doc_ids,
            all_centroids, doc_metadata,
        )

        # Check if any previous anomalies have been resolved
        self._anomaly_detector.check_resolutions(noise_doc_ids)

        # Phase 10: Detect emerging structures
        emerging = self._detect_emerging_structures(
            noise_doc_ids, space_vectors, space_doc_ids, all_centroids,
        )

        
#         Phase 11: Run critic scoring if available
        coherence_scores = {}
        if self.on_crystallize and "semantic" in space_vectors:
            semantic_vectors = space_vectors["semantic"]
            semantic_clusterer = self._cluster_engine.clusterers.get("semantic")
            
        # Phase 12: Build legacy cluster objects for backward compatibility
        self._build_legacy_clusters(
            space_vectors, space_doc_ids, coherence_scores,
        )

        # Phase 13: Assemble living ontology snapshot
        total_entities = sum(len(ents) for ents in doc_entities.values())
        total_rels = sum(len(rels) for rels in doc_relationships.values())
        total_docs = max(
            (len(ids) for ids in space_doc_ids.values()),
            default=0,
        )

        snapshot = LivingOntologySnapshot(
            snapshot_id=str(uuid.uuid4())[:16],
            generated_at=datetime.now(timezone.utc),
            corpus_stats=CorpusStats(
                total_documents=total_docs,
                total_entities=total_entities,
                total_relationships=total_rels,
                documents_since_last_snapshot=self._docs_since_last_full,
            ),
            clusters=detected_clusters,
            anomalies=self._anomaly_detector.get_unresolved(),
            emerging_structures=emerging,
            convergence_alerts=convergence_alerts,
            trajectories=trajectories,
            relational_gradients=gradients,
            processing_time_ms=0,  # set by caller
        )
        self._current_snapshot = snapshot

        # Persist
        if self._store_db:
            await self._persist_all(snapshot, detected_clusters, trajectories, anomalies, gradients)

        total_clusters = sum(
            s.get("n_clusters", 0)
            for s in cluster_stats.values()
            if isinstance(s, dict) and "n_clusters" in s
        )

        return {
            "status": "complete",
            "mode": "full" if full_recluster else "incremental",
            "n_clusters": total_clusters,
            "n_correlations": len(correlations),
            "n_trajectories": len(trajectories),
            "n_gradients": len(gradients),
            "n_anomalies": len(anomalies),
            "n_convergence_alerts": len(convergence_alerts),
            "n_emerging": len(emerging),
            "cluster_stats": cluster_stats,
        }

    async def _crystallize_legacy(self) -> dict:
        """Legacy single-space crystallization for backward compatibility."""
        start_time = time.monotonic()
        vectors = self.store.get_all_vectors()
        if vectors.shape[0] < 2:
            return {"status": "skipped", "reason": "insufficient_data"}

        doc_ids = self.store.get_all_ids()

        min_size = max(2, min(5, vectors.shape[0] // 10))
        labels, stats = run_clustering(vectors, min_cluster_size=min_size, min_samples=max(1, min_size - 1))
        self.labels = labels
        self.stats = stats

        cluster_map: dict[int, list[str]] = {}
        for i, label in enumerate(labels):
            label_int = int(label)
            if label_int == -1:
                continue
            cluster_map.setdefault(label_int, []).append(doc_ids[i])

        coherence_scores = {}
        if self.on_crystallize:
            coherence_scores = await self.on_crystallize(vectors, labels)

        self.clusters = [
            Cluster(
                id=cid,
                document_ids=members,
                coherence_score=coherence_scores.get(cid),
            )
            for cid, members in cluster_map.items()
        ]

        docs = [self.documents.get(did, Document(id=did, content="")) for did in doc_ids]
        self.graph.build_from_clusters(docs, doc_ids, labels, vectors, coherence_scores)

        self.last_run = datetime.now(timezone.utc)

        # Build and persist a living ontology snapshot (same as multi-space path)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        detected_clusters = [
            DetectedCluster(
                cluster_id=f"semantic_{cid}",
                primary_space="semantic",
                size=len(members),
                member_document_ids=members,
                label=f"Cluster {cid} ({len(members)} documents)",
                status="stable",
            )
            for cid, members in cluster_map.items()
        ]

        noise_count = int(np.sum(labels == -1))
        anomalies: list[Anomaly] = []
        for i, label in enumerate(labels):
            if int(label) == -1:
                anomalies.append(Anomaly(
                    anomaly_id=f"legacy_noise_{doc_ids[i][:8]}",
                    document_id=doc_ids[i],
                    anomaly_type="structural",
                    anomaly_score=0.5,
                    outlier_spaces=["semantic"],
                ))

        snapshot = LivingOntologySnapshot(
            snapshot_id=str(uuid.uuid4())[:16],
            generated_at=datetime.now(timezone.utc),
            corpus_stats=CorpusStats(
                total_documents=len(doc_ids),
                total_entities=0,
                total_relationships=0,
                documents_since_last_snapshot=self._docs_since_last_full,
            ),
            clusters=detected_clusters,
            anomalies=anomalies[:100],
            processing_time_ms=elapsed_ms,
        )
        self._current_snapshot = snapshot

        if self._store_db:
            try:
                await self._store_db.save_snapshot(snapshot)
                await self._store_db.save_clusters_batch(detected_clusters)
                await self._store_db.save_anomalies_batch(anomalies[:100])
            except Exception:
                logger.exception("legacy_crystallizer_persist_failed")

        return stats

    def _should_full_recluster(self) -> bool:
        """Determine if a full reclustering is needed."""
        return True

        if self._docs_since_last_full >= self._full_recluster_interval_docs:
            return True

        elapsed = (datetime.now(timezone.utc) - self._last_full_recluster).total_seconds()
        if elapsed >= self._full_recluster_interval_seconds:
            return True

        return False

    def _build_detected_clusters(
        self,
        correlations: list[dict[str, Any]],
        space_vectors: dict[str, np.ndarray],
        space_doc_ids: dict[str, list[str]],
        doc_entities: dict[str, list[dict[str, Any]]],
        doc_relationships: dict[str, list[dict[str, Any]]],
    ) -> list[DetectedCluster]:
        """Build DetectedCluster objects from correlation records."""
        seen_members: dict[frozenset[str], DetectedCluster] = {}
        densities_by_space = {
            space: clusterer.get_cluster_densities()
            for space, clusterer in self._cluster_engine.clusterers.items()
        }

        for corr in correlations:
            members = frozenset(corr["member_document_ids"])
            existing = seen_members.get(members)

            if existing and existing.cross_space_coherence >= corr["cross_space_coherence"]:
                continue

            cluster_id = corr["cluster_id"]
            primary_space = corr["primary_space"]
            member_list = corr["member_document_ids"]

            key_ents = extract_key_entities(member_list, doc_entities)
            key_rels = extract_key_relationships(member_list, doc_relationships)

            label = generate_label(key_ents, key_rels, corr["size"])

            # Compute centroid from primary space
            centroid: list[float] = []
            clusterer = self._cluster_engine.clusterers.get(primary_space)
            if clusterer:
                vectors = space_vectors.get(primary_space)
                doc_ids = space_doc_ids.get(primary_space, [])
                if vectors is not None:
                    indices = [
                        doc_ids.index(did)
                        for did in member_list
                        if did in doc_ids
                    ]
                    if indices:
                        centroid = vectors[indices].mean(axis=0).tolist()

            # Get density
            parts = cluster_id.rsplit("_", 1)
            numeric_id = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
            density = densities_by_space.get(primary_space, {}).get(numeric_id, 0.0)

            # Determine status
            prev_members = self._previous_cluster_members.get(cluster_id, set())
            if not prev_members:
                status = "forming"
            elif len(members) > len(prev_members) * 1.1:
                status = "growing"
            elif len(members) < len(prev_members) * 0.9:
                status = "shrinking"
            else:
                status = "stable"

            correlated_raw = corr.get("correlated_clusters", {})
            cluster_corr = ClusterCorrelation(
                semantic=correlated_raw.get("semantic"),
                entity=correlated_raw.get("entity"),
                relational=correlated_raw.get("relational"),
                temporal=correlated_raw.get("temporal"),
                geospatial=correlated_raw.get("geospatial"),
            )

            detected = DetectedCluster(
                cluster_id=cluster_id,
                primary_space=primary_space,
                correlated_clusters=cluster_corr,
                cross_space_coherence=corr["cross_space_coherence"],
                member_document_ids=member_list,
                size=corr["size"],
                density=density,
                stability=1.0 if status == "stable" else 0.7,
                centroid=centroid,
                label=label,
                status=status,
                key_entities=key_ents[:10],
                key_relationships=key_rels[:5],
            )

            seen_members[members] = detected

        return list(seen_members.values())

    def _track_cluster_lifecycle(self, clusters: list[DetectedCluster]) -> None:
        """Match current clusters to previous run using member overlap."""
        current_members: dict[str, set[str]] = {}
        for cluster in clusters:
            current_members[cluster.cluster_id] = set(cluster.member_document_ids)

        self._previous_cluster_members = current_members

    def _detect_emerging_structures(
        self,
        noise_doc_ids: dict[str, list[str]],
        space_vectors: dict[str, np.ndarray],
        space_doc_ids: dict[str, list[str]],
        all_centroids: dict[str, dict[int, np.ndarray]],
    ) -> list[EmergingStructure]:
        """Detect regions where clusters may be forming from noise points."""
        emerging: list[EmergingStructure] = []

        for space, noise_ids in noise_doc_ids.items():
            if len(noise_ids) < 3:
                continue

            doc_ids_list = space_doc_ids.get(space, [])
            vectors = space_vectors.get(space)
            if vectors is None:
                continue

            noise_indices = [
                doc_ids_list.index(did)
                for did in noise_ids
                if did in doc_ids_list
            ]
            if len(noise_indices) < 3:
                continue

            noise_vectors = vectors[noise_indices]
            centroid = noise_vectors.mean(axis=0)
            distances = np.linalg.norm(noise_vectors - centroid, axis=1)
            avg_dist = float(np.mean(distances))

            cluster_centroids = all_centroids.get(space, {})
            if cluster_centroids:
                cluster_spreads = []
                clusterer = self._cluster_engine.clusterers.get(space)
                if clusterer:
                    members = clusterer.get_cluster_members()
                    for cid, indices in members.items():
                        if cid in cluster_centroids:
                            cluster_vecs = vectors[indices]
                            c = cluster_centroids[cid]
                            spread = float(np.mean(np.linalg.norm(cluster_vecs - c, axis=1)))
                            cluster_spreads.append(spread)

                avg_cluster_spread = np.mean(cluster_spreads) if cluster_spreads else avg_dist * 2

                if avg_dist < avg_cluster_spread * 1.5 and len(noise_ids) >= 3:
                    emerging.append(EmergingStructure(
                        region_id=f"emerging_{space}_{str(uuid.uuid4())[:6]}",
                        space=space,
                        centroid=centroid.tolist(),
                        density=1.0 / max(avg_dist, 0.001),
                        density_trend="increasing",
                        candidate_entities=noise_ids[:20],
                        formation_confidence=min(1.0, len(noise_ids) / 10.0),
                    ))

        return emerging

    def _build_legacy_clusters(
        self,
        space_vectors: dict[str, np.ndarray],
        space_doc_ids: dict[str, list[str]],
        coherence_scores: dict[int, float],
    ) -> None:
        """Build legacy Cluster objects and OntologyGraph for backward compat."""
        semantic_clusterer = self._cluster_engine.clusterers.get("semantic")
        if semantic_clusterer is None:
            return

        labels = semantic_clusterer.labels
        if labels is None:
            return

        vectors = space_vectors.get("semantic")
        doc_ids = space_doc_ids.get("semantic", [])
        if vectors is None:
            return

        self.labels = labels

        cluster_map: dict[int, list[str]] = {}
        for i, label in enumerate(labels):
            label_int = int(label)
            if label_int == -1:
                continue
            cluster_map.setdefault(label_int, []).append(doc_ids[i])

        self.clusters = [
            Cluster(
                id=cid,
                document_ids=members,
                coherence_score=coherence_scores.get(cid),
            )
            for cid, members in cluster_map.items()
        ]

        docs = [
            self.documents.get(did, Document(id=did, content=""))
            for did in doc_ids
        ]
        self.graph.build_from_clusters(docs, doc_ids, labels, vectors, coherence_scores)

    async def _load_enrichment_context(
        self,
        space_doc_ids: dict[str, list[str]],
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, list[dict[str, Any]]],
        dict[str, dict[str, Any]],
    ]:
        """Load entity, relationship, and metadata context for all documents."""
        doc_entities: dict[str, list[dict[str, Any]]] = {}
        doc_relationships: dict[str, list[dict[str, Any]]] = {}
        doc_metadata: dict[str, dict[str, Any]] = {}

        all_doc_ids: set[str] = set()
        for doc_ids in space_doc_ids.values():
            all_doc_ids.update(doc_ids)

        if not self._store_db or not all_doc_ids:
            return doc_entities, doc_relationships, doc_metadata

        try:
            from periphery.db import get_connection

            db_path = self._store_db._db_path
            async with get_connection(db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")

                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='document_enrichments'"
                )
                if not await cursor.fetchone():
                    return doc_entities, doc_relationships, doc_metadata

                batch_size = 500
                doc_id_list = sorted(all_doc_ids)

                for i in range(0, len(doc_id_list), batch_size):
                    batch = doc_id_list[i:i + batch_size]
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
                        entities = []
                        relationships = []

                        if row[1]:
                            try:
                                entities = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                            except (json.JSONDecodeError, TypeError):
                                pass

                        if row[2]:
                            try:
                                relationships = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                            except (json.JSONDecodeError, TypeError):
                                pass

                        doc_entities[did] = entities if isinstance(entities, list) else []
                        doc_relationships[did] = relationships if isinstance(relationships, list) else []

                # Load source credibility
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
                )
                if await cursor.fetchone():
                    for i in range(0, len(doc_id_list), batch_size):
                        batch = doc_id_list[i:i + batch_size]
                        placeholders = ",".join("?" for _ in batch)

                        cursor = await db.execute(
                            f"""
                            SELECT id, source_credibility_tier
                            FROM documents
                            WHERE id IN ({placeholders})
                            """,
                            batch,
                        )

                        for row in await cursor.fetchall():
                            did = row[0]
                            doc_metadata[did] = {
                                "source_credibility_tier": row[1] or 4,
                                "entities": doc_entities.get(did, []),
                                "relationships": doc_relationships.get(did, []),
                            }

        except Exception:
            logger.debug("enrichment_context_load_partial")

        return doc_entities, doc_relationships, doc_metadata

    async def _persist_all(
        self,
        snapshot: LivingOntologySnapshot,
        clusters: list[DetectedCluster],
        trajectories: list[Trajectory],
        anomalies: list[Anomaly],
        gradients: list[RelationalGradient],
    ) -> None:
        """Persist all crystallizer state to SQLite."""
        if not self._store_db:
            return

        try:
            await self._store_db.save_snapshot(snapshot)
            await self._store_db.save_clusters_batch(clusters)

            for traj in trajectories:
                await self._store_db.save_trajectory(traj)

            await self._store_db.save_anomalies_batch(anomalies)
            await self._store_db.save_gradients(gradients)

        except Exception:
            logger.exception("crystallizer_persist_failed")
