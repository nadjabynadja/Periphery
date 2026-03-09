"""Crystallizer persistence — SQLite storage for snapshots, clusters, and history.

Manages all Crystallizer state tables:
  - crystallizer_snapshots: full ontology snapshots
  - clusters: persistent cluster records
  - cluster_snapshots: point-in-time cluster measurements
  - trajectories: detected trajectories
  - anomalies: detected anomalies
  - relational_gradients: inter-cluster relationships
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_connection
import structlog

from periphery.crystallizer.models import (
    Anomaly,
    DetectedCluster,
    LivingOntologySnapshot,
    RelationalGradient,
    Trajectory,
)

logger = structlog.get_logger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crystallizer_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    generated_at TIMESTAMP,
    snapshot_data JSON,
    corpus_size INTEGER,
    num_clusters INTEGER,
    num_anomalies INTEGER,
    processing_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_snapshot_time ON crystallizer_snapshots(generated_at);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id TEXT PRIMARY KEY,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    status TEXT,
    current_size INTEGER,
    cross_space_coherence FLOAT,
    label TEXT,
    key_entities JSON,
    metadata JSON
);

CREATE TABLE IF NOT EXISTS cluster_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT REFERENCES clusters(cluster_id),
    timestamp TIMESTAMP,
    size INTEGER,
    centroid JSON,
    coherence FLOAT
);

CREATE INDEX IF NOT EXISTS idx_cluster_snap_time ON cluster_snapshots(cluster_id, timestamp);

CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id TEXT PRIMARY KEY,
    cluster_id TEXT REFERENCES clusters(cluster_id),
    space TEXT,
    pattern TEXT,
    velocity FLOAT,
    confidence FLOAT,
    first_detected TIMESTAMP,
    last_updated TIMESTAMP,
    snapshots JSON
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    document_id TEXT,
    anomaly_type TEXT,
    anomaly_score FLOAT,
    outlier_spaces JSON,
    source_credibility INTEGER,
    first_detected TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_into_cluster TEXT
);

CREATE INDEX IF NOT EXISTS idx_anomaly_unresolved ON anomalies(resolved, anomaly_score DESC);

CREATE TABLE IF NOT EXISTS relational_gradients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_cluster TEXT REFERENCES clusters(cluster_id),
    target_cluster TEXT REFERENCES clusters(cluster_id),
    gradient_score FLOAT,
    components JSON,
    first_detected TIMESTAMP,
    trend TEXT
);

CREATE INDEX IF NOT EXISTS idx_gradient_score ON relational_gradients(gradient_score DESC);
"""


class CrystallizerStore:
    """Async SQLite persistence for all Crystallizer state."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA_SQL)
            await db.commit()
        self._initialized = True
        logger.info("crystallizer_store_initialized", db_path=self._db_path)

    async def save_snapshot(self, snapshot: LivingOntologySnapshot) -> None:
        """Persist a full ontology snapshot."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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

            # Prune old snapshots — keep last 100
            await db.execute(
                """
                DELETE FROM crystallizer_snapshots
                WHERE snapshot_id NOT IN (
                    SELECT snapshot_id FROM crystallizer_snapshots
                    ORDER BY generated_at DESC LIMIT 100
                )
                """
            )
            await db.commit()

    async def load_latest_snapshot(self) -> LivingOntologySnapshot | None:
        """Load the most recent ontology snapshot."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            # Check if cluster exists
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

            # Save cluster snapshot
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")

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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
                        for s in trajectory.snapshots[-20:]  # Keep last 20 snapshots
                    ]),
                ),
            )
            await db.commit()

    async def save_anomaly(self, anomaly: Anomaly) -> None:
        """Upsert an anomaly record."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            # Clear old gradients (they're regenerated each run)
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            placeholders = ",".join("?" for _ in cluster_ids)
            await db.execute(
                f"UPDATE clusters SET status = 'dissolved', last_seen = ? "
                f"WHERE cluster_id IN ({placeholders})",
                [now, *cluster_ids],
            )
            await db.commit()

    async def get_cluster_history(self, cluster_id: str) -> list[dict[str, Any]]:
        """Get the size history for a cluster."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                "SELECT cluster_id FROM clusters WHERE status != 'dissolved'"
            )
            return {r[0] for r in await cursor.fetchall()}

    async def get_telemetry(self) -> dict[str, Any]:
        """Return monitoring telemetry for the Crystallizer."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            # Cluster counts by status
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM clusters GROUP BY status"
            )
            cluster_by_status = {r[0]: r[1] for r in await cursor.fetchall()}

            # Trajectory counts by pattern
            cursor = await db.execute(
                "SELECT pattern, COUNT(*) FROM trajectories GROUP BY pattern"
            )
            traj_by_pattern = {r[0]: r[1] for r in await cursor.fetchall()}

            # Unresolved anomaly counts by type
            cursor = await db.execute(
                "SELECT anomaly_type, COUNT(*) FROM anomalies "
                "WHERE resolved = FALSE GROUP BY anomaly_type"
            )
            anomaly_by_type = {r[0]: r[1] for r in await cursor.fetchall()}

            # Top 10 gradients
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

            # Last snapshot time
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
