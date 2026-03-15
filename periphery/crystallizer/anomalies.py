"""Anomaly detection — find data points that don't fit any existing pattern.

Identifies outliers across embedding spaces, scores their anomalousness
based on multi-space consistency, distance to nearest cluster, and source
credibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from periphery.crystallizer.models import Anomaly

logger = structlog.get_logger(__name__)


class AnomalyDetector:
    """Detects and scores anomalous documents that don't belong to any cluster.

    HDBSCAN naturally identifies outliers (label -1). This detector scores
    them on multi-space consistency, distance to nearest cluster, and
    source credibility to surface the most interesting signals.
    """

    def __init__(self) -> None:
        self._known_anomalies: dict[str, Anomaly] = {}

    @property
    def anomalies(self) -> dict[str, Anomaly]:
        return self._known_anomalies

    def detect(
        self,
        noise_doc_ids: dict[str, list[str]],
        space_vectors: dict[str, np.ndarray],
        space_doc_ids: dict[str, list[str]],
        space_centroids: dict[str, dict[int, np.ndarray]],
        doc_metadata: dict[str, dict[str, Any]],
    ) -> list[Anomaly]:
        """Score all outlier documents across spaces.

        Args:
            noise_doc_ids: {space: [doc_ids flagged as noise]}
            space_vectors: {space: vectors_array}
            space_doc_ids: {space: [doc_id, ...]}
            space_centroids: {space: {cluster_id: centroid_vector}}
            doc_metadata: {doc_id: {source_credibility_tier, entities, relationships, ...}}

        Returns:
            List of Anomaly objects for significant outliers.
        """
        # Count how many spaces each doc is an outlier in
        doc_outlier_spaces: dict[str, list[str]] = {}
        for space, doc_ids in noise_doc_ids.items():
            for did in doc_ids:
                doc_outlier_spaces.setdefault(did, []).append(space)

        all_spaces = set(noise_doc_ids.keys())
        anomalies: list[Anomaly] = []

        # Pre-build doc_id -> index lookup dicts for O(1) access
        doc_id_index: dict[str, dict[str, int]] = {
            space: {did: i for i, did in enumerate(ids)}
            for space, ids in space_doc_ids.items()
        }

        for doc_id, outlier_spaces in doc_outlier_spaces.items():
            # Multi-space anomaly consistency
            multi_space_ratio = len(outlier_spaces) / len(all_spaces) if all_spaces else 0

            # Distance to nearest cluster (averaged across outlier spaces)
            avg_distance = 0.0
            nearest_cluster = ""
            min_distance = float("inf")

            for space in outlier_spaces:
                centroids = space_centroids.get(space, {})
                idx_map = doc_id_index.get(space, {})
                vectors = space_vectors.get(space)

                if vectors is None or doc_id not in idx_map or not centroids:
                    continue

                doc_idx = idx_map[doc_id]
                doc_vec = vectors[doc_idx]

                for cid, centroid in centroids.items():
                    dist = float(np.linalg.norm(doc_vec - centroid))
                    if dist < min_distance:
                        min_distance = dist
                        nearest_cluster = f"{space}_{cid}"

            avg_distance = min_distance if min_distance < float("inf") else 0.0

            # Source credibility
            meta = doc_metadata.get(doc_id, {})
            credibility = meta.get("source_credibility_tier", 4)

            # Classify anomaly type
            anomaly_type = self._classify_anomaly(doc_id, outlier_spaces, meta)

            # Compute composite score
            score = self._compute_score(
                multi_space_ratio, avg_distance, credibility, len(outlier_spaces)
            )

            if score < 0.1:
                continue  # Skip low-score anomalies

            # Check if we already know this anomaly
            existing = self._known_anomalies.get(doc_id)
            anomaly_id = existing.anomaly_id if existing else str(uuid.uuid4())[:12]
            first_detected = existing.first_detected if existing else datetime.now(timezone.utc)

            anomaly = Anomaly(
                anomaly_id=anomaly_id,
                document_id=doc_id,
                anomaly_type=anomaly_type,
                anomaly_score=score,
                outlier_spaces=outlier_spaces,
                nearest_cluster=nearest_cluster,
                distance_to_nearest=avg_distance,
                source_credibility=credibility,
                first_detected=first_detected,
                resolved=False,
                description=(
                    f"{anomaly_type} anomaly (score={score:.2f}) "
                    f"in spaces {outlier_spaces}, nearest cluster: {nearest_cluster}"
                ),
            )

            self._known_anomalies[doc_id] = anomaly
            anomalies.append(anomaly)

        logger.info(
            "anomalies_detected",
            total_outlier_docs=len(doc_outlier_spaces),
            significant_anomalies=len(anomalies),
        )

        return anomalies

    def resolve_anomaly(self, doc_id: str, cluster_id: str) -> None:
        """Mark an anomaly as resolved into a cluster."""
        if doc_id in self._known_anomalies:
            self._known_anomalies[doc_id].resolved = True
            self._known_anomalies[doc_id].resolved_into_cluster = cluster_id

    def check_resolutions(
        self,
        noise_doc_ids: dict[str, list[str]],
    ) -> list[str]:
        """Check if any known anomalies have been absorbed into clusters.

        Returns list of doc_ids that were resolved.
        """
        all_noise = set()
        for doc_ids in noise_doc_ids.values():
            all_noise.update(doc_ids)

        resolved: list[str] = []
        for doc_id, anomaly in self._known_anomalies.items():
            if anomaly.resolved:
                continue
            if doc_id not in all_noise:
                # No longer an outlier — it got absorbed into a cluster
                anomaly.resolved = True
                resolved.append(doc_id)

        return resolved

    def get_unresolved(self) -> list[Anomaly]:
        """Return all unresolved anomalies."""
        return [a for a in self._known_anomalies.values() if not a.resolved]

    def _classify_anomaly(
        self,
        doc_id: str,
        outlier_spaces: list[str],
        metadata: dict[str, Any],
    ) -> str:
        """Classify the type of anomaly based on which spaces flagged it."""
        entities = metadata.get("entities", [])
        relationships = metadata.get("relationships", [])

        # Check for novel entities — if entity space is an outlier
        if "entity" in outlier_spaces and entities:
            return "novel_entity"

        # Check for novel relationships
        if "relational" in outlier_spaces and relationships:
            return "novel_relationship"

        # Geographic outlier
        if "geospatial" in outlier_spaces and "semantic" not in outlier_spaces:
            return "geographic"

        # Temporal outlier
        if "temporal" in outlier_spaces and "semantic" not in outlier_spaces:
            return "temporal"

        # Structural anomaly — outlier in multiple spaces simultaneously
        if len(outlier_spaces) >= 3:
            return "structural"

        # Default
        return "structural"

    def _compute_score(
        self,
        multi_space_ratio: float,
        distance: float,
        credibility: int,
        num_outlier_spaces: int,
    ) -> float:
        """Compute composite anomaly score (0.0-1.0)."""
        # Multi-space consistency (more spaces = more anomalous)
        space_score = min(1.0, multi_space_ratio * 1.5)

        # Distance score (farther from clusters = more anomalous)
        # Normalize distance — cap at reasonable value
        dist_score = min(1.0, distance / 10.0)

        # Credibility bonus (higher credibility sources get boosted)
        # Tier 1 = highest credibility = biggest boost
        cred_multiplier = {1: 1.3, 2: 1.1, 3: 1.0, 4: 0.8}.get(credibility, 0.8)

        raw_score = (
            0.40 * space_score
            + 0.35 * dist_score
            + 0.25 * (num_outlier_spaces / 5.0)
        )

        return min(1.0, raw_score * cred_multiplier)
