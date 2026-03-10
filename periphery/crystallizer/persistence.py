"""Crystallizer persistence — SQLite storage for snapshots, clusters, and history.

Uses the shared DatabasePool for all connections. Schema is defined
centrally in periphery/db.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_pool
import structlog

from periphery.crystallizer.models import (
    Anomaly,
    DetectedCluster,
    LivingOntologySnapshot,
    RelationalGradient,
    Trajectory,
)

logger = structlog.get_logger(__name__)


class CrystallizerStore:
    """Async SQLite persistence for all Crystallizer state.

    Uses the shared connection pool — no local connection management.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        """Verify pool is available. Schema is managed by db.py."""
        get_pool()  # raises if not initialized
        logger.info("crystallizer_store_initialized", db_path=self._db_path)

    async def save_snapshot(self, snapshot: LivingOntologySnapshot) -> None:
        """Persist a full ontology snapshot."""
        pool = get_pool()
        async with pool.acquire() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO crystallizer_snapshots
                    (snapshot_id, generated_at, snapshot_data, corpus_size,
                     num_clusters, num_anomalies, processing_time_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.generated_at.isoformat(),
                    snapshot.model_dump_json(),
                    snapshot.corpus_stats.total_documents,
                    len(snapshot.clusters),
                    len(snapshot.anomalies),
                    snapshot.processing_time_ms,
                ),
            )
            await db.commit()

    async def load_latest_snapshot(self) -> LivingOntologySnapshot | None:
        """Load the most recent ontology snapshot."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT snapshot_data FROM crystallizer_snapshots "
                "ORDER BY generated_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return LivingOntologySnapshot.model_validate_json(row[0])
        return None

    async def save_cluster(self, cluster: DetectedCluster) -> None:
        """Upsert a cluster record."""
        now = datetime.now(timezone.utc).isoformat()
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT first_seen FROM clusters WHERE cluster_id = ?",
                (cluster.cluster_id,),
            )
            row = await cursor.fetchone()
            first_seen = row[0] if row else now

            await db.execute(
                """
                INSERT OR REPLACE INTO clusters
                    (cluster_id, first_seen, last_seen, status, current_size,
                     cross_space_coherence, label, key_entities, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cluster.cluster_id,
                    first_seen,
                    now,
                    cluster.status,
                    cluster.size,
                    cluster.cross_space_coherence,
                    cluster.label,
                    json.dumps(cluster.key_entities),
                    json.dumps({
                        "primary_space": cluster.primary_space,
                        "density": cluster.density,
                        "stability": cluster.stability,
                    }),
                ),
            )

            await db.execute(
                """
                INSERT INTO cluster_snapshots (cluster_id, timestamp, size, centroid, coherence)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cluster.cluster_id,
                    now,
                    cluster.size,
                    json.dumps(cluster.centroid[:10]) if cluster.centroid else "[]",
                    cluster.cross_space_coherence,
                ),
            )
            await db.commit()

    async def save_clusters_batch(self, clusters: list[DetectedCluster]) -> None:
        """Save multiple clusters in a single transaction."""
        if not clusters:
            return
        now = datetime.now(timezone.utc).isoformat()
        pool = get_pool()
        async with pool.acquire() as db:
            for cluster in clusters:
                cursor = await db.execute(
                    "SELECT first_seen FROM clusters WHERE cluster_id = ?",
                    (cluster.cluster_id,),
                )
                row = await cursor.fetchone()
                first_seen = row[0] if row else now

                await db.execute(
                    """
                    INSERT OR REPLACE INTO clusters
                        (cluster_id, first_seen, last_seen, status, current_size,
                         cross_space_coherence, label, key_entities, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cluster.cluster_id,
                        first_seen,
                        now,
                        cluster.status,
                        cluster.size,
                        cluster.cross_space_coherence,
                        cluster.label,
                        json.dumps(cluster.key_entities),
                        json.dumps({
                            "primary_space": cluster.primary_space,
                            "density": cluster.density,
                            "stability": cluster.stability,
                        }),
                    ),
                )

                await db.execute(
                    """
                    INSERT INTO cluster_snapshots (cluster_id, timestamp, size, centroid, coherence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        cluster.cluster_id,
                        now,
                        cluster.size,
                        json.dumps(cluster.centroid[:10]) if cluster.centroid else "[]",
                        cluster.cross_space_coherence,
                    ),
                )

            await db.commit()

    async def save_trajectory(self, trajectory: Trajectory) -> None:
        """Upsert a trajectory record."""
        now = datetime.now(timezone.utc).isoformat()
        pool = get_pool()
        async with pool.acquire() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO trajectories
                    (trajectory_id, cluster_id, space, pattern, velocity,
                     confidence, first_detected, last_updated, snapshots)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory.trajectory_id,
                    trajectory.cluster_id,
                    trajectory.space,
                    trajectory.pattern,
                    trajectory.velocity,
                    trajectory.confidence,
                    trajectory.first_detected.isoformat(),
                    now,
                    json.dumps([
                        {"timestamp": s.timestamp.isoformat(), "centroid": s.centroid[:10]}
                        for s in trajectory.snapshots[-20:]
                    ]),
                ),
            )
            await db.commit()

    async def save_anomaly(self, anomaly: Anomaly) -> None:
        """Upsert an anomaly record."""
        pool = get_pool()
        async with pool.acquire() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO anomalies
                    (anomaly_id, document_id, anomaly_type, anomaly_score,
                     outlier_spaces, source_credibility, first_detected,
                     resolved, resolved_into_cluster)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anomaly.anomaly_id,
                    anomaly.document_id,
                    anomaly.anomaly_type,
                    anomaly.anomaly_score,
                    json.dumps(anomaly.outlier_spaces),
                    anomaly.source_credibility,
                    anomaly.first_detected.isoformat(),
                    anomaly.resolved,
                    anomaly.resolved_into_cluster,
                ),
            )
            await db.commit()

    async def save_anomalies_batch(self, anomalies: list[Anomaly]) -> None:
        """Save multiple anomalies in a single transaction."""
        if not anomalies:
            return
        pool = get_pool()
        async with pool.acquire() as db:
            for anomaly in anomalies:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO anomalies
                        (anomaly_id, document_id, anomaly_type, anomaly_score,
                         outlier_spaces, source_credibility, first_detected,
                         resolved, resolved_into_cluster)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anomaly.anomaly_id,
                        anomaly.document_id,
                        anomaly.anomaly_type,
                        anomaly.anomaly_score,
                        json.dumps(anomaly.outlier_spaces),
                        anomaly.source_credibility,
                        anomaly.first_detected.isoformat(),
                        anomaly.resolved,
                        anomaly.resolved_into_cluster,
                    ),
                )
            await db.commit()

    async def save_gradients(self, gradients: list[RelationalGradient]) -> None:
        """Replace all gradient records with current set."""
        pool = get_pool()
        async with pool.acquire() as db:
            await db.execute("DELETE FROM relational_gradients")
            for g in gradients:
                await db.execute(
                    """
                    INSERT INTO relational_gradients
                        (source_cluster, target_cluster, gradient_score,
                         components, first_detected, trend)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        g.source_cluster,
                        g.target_cluster,
                        g.gradient_score,
                        g.components.model_dump_json(),
                        g.first_detected.isoformat(),
                        g.gradient_trend,
                    ),
                )
            await db.commit()

    async def mark_clusters_dissolved(self, cluster_ids: list[str]) -> None:
        """Mark clusters as dissolved."""
        if not cluster_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        pool = get_pool()
        async with pool.acquire() as db:
            placeholders = ",".join("?" for _ in cluster_ids)
            await db.execute(
                f"UPDATE clusters SET status = 'dissolved', last_seen = ? "
                f"WHERE cluster_id IN ({placeholders})",
                [now, *cluster_ids],
            )
            await db.commit()

    async def get_cluster_history(self, cluster_id: str) -> list[dict[str, Any]]:
        """Get the size history for a cluster."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT timestamp, size, coherence FROM cluster_snapshots "
                "WHERE cluster_id = ? ORDER BY timestamp",
                (cluster_id,),
            )
            return [
                {"timestamp": r[0], "size": r[1], "coherence": r[2]}
                for r in await cursor.fetchall()
            ]

    async def get_active_cluster_ids(self) -> set[str]:
        """Return IDs of all non-dissolved clusters."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT cluster_id FROM clusters WHERE status != 'dissolved'"
            )
            return {r[0] for r in await cursor.fetchall()}

    async def get_telemetry(self) -> dict[str, Any]:
        """Return monitoring telemetry for the Crystallizer."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM clusters GROUP BY status"
            )
            cluster_by_status = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT pattern, COUNT(*) FROM trajectories GROUP BY pattern"
            )
            traj_by_pattern = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT anomaly_type, COUNT(*) FROM anomalies "
                "WHERE resolved = FALSE GROUP BY anomaly_type"
            )
            anomaly_by_type = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT source_cluster, target_cluster, gradient_score, trend "
                "FROM relational_gradients ORDER BY gradient_score DESC LIMIT 10"
            )
            top_gradients = [
                {
                    "source": r[0],
                    "target": r[1],
                    "score": r[2],
                    "trend": r[3],
                }
                for r in await cursor.fetchall()
            ]

            cursor = await db.execute(
                "SELECT generated_at FROM crystallizer_snapshots "
                "ORDER BY generated_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            last_snapshot = row[0] if row else None

            return {
                "clusters_by_status": cluster_by_status,
                "trajectories_by_pattern": traj_by_pattern,
                "unresolved_anomalies_by_type": anomaly_by_type,
                "top_gradients": top_gradients,
                "last_snapshot_at": last_snapshot,
            }
