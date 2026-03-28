"""Cluster detection engine — HDBSCAN over multiple embedding spaces.

Performs independent density-based clustering in each embedding space, then
correlates clusters across spaces to identify high-confidence emergent
structures. Uses approximate_predict for incremental assignment between
full reclustering runs.
"""

from __future__ import annotations

from typing import Any

import hdbscan
import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class SpaceClusterer:
    """HDBSCAN clusterer for a single embedding space.

    Wraps the HDBSCAN model and provides incremental prediction via
    approximate_predict for new points between full reclustering runs.
    """

    def __init__(
        self,
        space: str,
        min_cluster_size: int = 5,
        min_samples: int = 3,
        cluster_selection_epsilon: float = 0.0,
    ) -> None:
        self.space = space
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.cluster_selection_epsilon = cluster_selection_epsilon
        self._clusterer: hdbscan.HDBSCAN | None = None
        self._labels: np.ndarray | None = None
        self._probabilities: np.ndarray | None = None
        self._doc_ids: list[str] = []

    @property
    def labels(self) -> np.ndarray | None:
        return self._labels

    @property
    def probabilities(self) -> np.ndarray | None:
        return self._probabilities

    @property
    def doc_ids(self) -> list[str]:
        return self._doc_ids

    @property
    def clusterer(self) -> hdbscan.HDBSCAN | None:
        return self._clusterer

    def fit(self, vectors: np.ndarray, doc_ids: list[str]) -> dict[str, Any]:
        """Run full HDBSCAN clustering over the embedding space.

        Returns clustering statistics.
        """
        self._doc_ids = doc_ids

        if vectors.shape[0] < self.min_cluster_size:
            self._labels = np.full(vectors.shape[0], -1)
            self._probabilities = np.zeros(vectors.shape[0])
            self._clusterer = None
            return {
                "space": self.space,
                "n_clusters": 0,
                "noise_count": vectors.shape[0],
                "noise_ratio": 1.0,
            }

        self._clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            min_samples=self.min_samples,
            cluster_selection_epsilon=self.cluster_selection_epsilon,
            metric="euclidean",
            cluster_selection_method="eom",
            prediction_data=True,
        )
        self._labels = self._clusterer.fit_predict(vectors)
        self._probabilities = self._clusterer.probabilities_

        n_clusters = len(set(self._labels)) - (1 if -1 in self._labels else 0)
        noise_count = int(np.sum(self._labels == -1))

        return {
            "space": self.space,
            "n_clusters": n_clusters,
            "noise_count": noise_count,
            "noise_ratio": noise_count / len(self._labels) if len(self._labels) > 0 else 0,
        }

    def predict(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Assign new points to existing clusters using approximate_predict.

        Returns (labels, strengths) for the new points.
        """
        if self._clusterer is None or vectors.shape[0] == 0:
            return np.full(vectors.shape[0], -1), np.zeros(vectors.shape[0])

        labels, strengths = hdbscan.approximate_predict(self._clusterer, vectors)
        return labels, strengths

    def get_cluster_members(self) -> dict[int, list[int]]:
        """Return mapping of cluster_id -> list of member indices."""
        if self._labels is None:
            return {}
        clusters: dict[int, list[int]] = {}
        for i, label in enumerate(self._labels):
            label_int = int(label)
            if label_int == -1:
                continue
            clusters.setdefault(label_int, []).append(i)
        return clusters

    def get_noise_indices(self) -> list[int]:
        """Return indices of noise points (label == -1)."""
        if self._labels is None:
            return []
        return [i for i, label in enumerate(self._labels) if label == -1]

    def get_cluster_centroids(self, vectors: np.ndarray) -> dict[int, np.ndarray]:
        """Compute centroid for each cluster."""
        members = self.get_cluster_members()
        centroids = {}
        max_idx = len(vectors)
        for cluster_id, indices in members.items():
            valid = [i for i in indices if i < max_idx]
            if not valid:
                continue
            centroids[cluster_id] = vectors[valid].mean(axis=0)
        return centroids

    def get_cluster_densities(self) -> dict[int, float]:
        """Compute average membership probability per cluster as density proxy."""
        if self._labels is None or self._probabilities is None:
            return {}
        members = self.get_cluster_members()
        densities = {}
        max_idx = len(self._probabilities)
        for cluster_id, indices in members.items():
            valid = [i for i in indices if i < max_idx]
            if not valid:
                continue
            probs = self._probabilities[valid]
            densities[cluster_id] = float(np.mean(probs))
        return densities


class MultiSpaceClusterEngine:
    """Orchestrates clustering across all embedding spaces and correlates results.

    Runs independent HDBSCAN in each space, then builds a cross-space
    correlation matrix to identify high-confidence emergent structures.
    """

    def __init__(
        self,
        min_cluster_size: int = 5,
        min_samples: int = 3,
        cluster_selection_epsilon: float = 0.0,
        member_overlap_threshold: float = 0.3,
    ) -> None:
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.cluster_selection_epsilon = cluster_selection_epsilon
        self.member_overlap_threshold = member_overlap_threshold
        self._clusterers: dict[str, SpaceClusterer] = {}

    @property
    def clusterers(self) -> dict[str, SpaceClusterer]:
        return self._clusterers

    def cluster_all_spaces(
        self,
        space_vectors: dict[str, np.ndarray],
        space_doc_ids: dict[str, list[str]],
    ) -> dict[str, dict[str, Any]]:
        """Run HDBSCAN in each embedding space independently.

        Args:
            space_vectors: {space_name: vectors_array}
            space_doc_ids: {space_name: [doc_id, ...]}

        Returns:
            Per-space clustering statistics.
        """
        stats: dict[str, dict[str, Any]] = {}

        for space, vectors in space_vectors.items():
            doc_ids = space_doc_ids.get(space, [])
            if vectors.shape[0] == 0:
                continue

            # Adaptive min_cluster_size for small datasets
            adaptive_min = max(2, min(self.min_cluster_size, vectors.shape[0] // 10))
            adaptive_samples = max(1, min(self.min_samples, adaptive_min - 1))

            clusterer = SpaceClusterer(
                space=space,
                min_cluster_size=adaptive_min,
                min_samples=adaptive_samples,
                cluster_selection_epsilon=self.cluster_selection_epsilon,
            )
            space_stats = clusterer.fit(vectors, doc_ids)
            self._clusterers[space] = clusterer
            stats[space] = space_stats

            logger.info(
                "space_clustered",
                space=space,
                n_clusters=space_stats["n_clusters"],
                noise_ratio=round(space_stats["noise_ratio"], 3),
                total_points=vectors.shape[0],
            )

        return stats

    def predict_incremental(
        self,
        space_vectors: dict[str, np.ndarray],
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Assign new points to existing clusters using approximate_predict.

        Returns {space: (labels, strengths)} for each space.
        """
        results: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for space, vectors in space_vectors.items():
            clusterer = self._clusterers.get(space)
            if clusterer is None:
                results[space] = (np.full(vectors.shape[0], -1), np.zeros(vectors.shape[0]))
            else:
                results[space] = clusterer.predict(vectors)
        return results

    def correlate_clusters(
        self,
        space_doc_ids: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Correlate clusters across embedding spaces.

        For each cluster in each space, find overlapping clusters in other
        spaces based on member document overlap. Returns a list of
        cross-space cluster correlation records.
        """
        # Build per-space cluster membership: {space: {cluster_id: set(doc_ids)}}
        space_memberships: dict[str, dict[int, set[str]]] = {}

        for space, clusterer in self._clusterers.items():
            doc_ids = space_doc_ids.get(space, [])
            members = clusterer.get_cluster_members()
            space_memberships[space] = {}
            for cid, indices in members.items():
                space_memberships[space][cid] = {doc_ids[i] for i in indices if i < len(doc_ids)}

        # Build correlations
        correlations: list[dict[str, Any]] = []
        all_spaces = list(self._clusterers.keys())

        for primary_space in all_spaces:
            memberships = space_memberships.get(primary_space, {})
            for cluster_id, members in memberships.items():
                if not members:
                    continue

                correlated: dict[str, str | None] = {s: None for s in all_spaces}
                correlated[primary_space] = f"{primary_space}_{cluster_id}"
                spaces_with_match = 1  # primary space always matches

                for other_space in all_spaces:
                    if other_space == primary_space:
                        continue
                    other_memberships = space_memberships.get(other_space, {})
                    best_overlap = 0.0
                    best_cid = None

                    for other_cid, other_members in other_memberships.items():
                        if not other_members:
                            continue
                        overlap = len(members & other_members)
                        # Jaccard-like overlap relative to smaller cluster
                        min_size = min(len(members), len(other_members))
                        overlap_ratio = overlap / min_size if min_size > 0 else 0
                        if overlap_ratio > best_overlap and overlap_ratio >= self.member_overlap_threshold:
                            best_overlap = overlap_ratio
                            best_cid = other_cid

                    if best_cid is not None:
                        correlated[other_space] = f"{other_space}_{best_cid}"
                        spaces_with_match += 1

                coherence = spaces_with_match / len(all_spaces) if all_spaces else 0

                correlations.append({
                    "cluster_id": f"{primary_space}_{cluster_id}",
                    "primary_space": primary_space,
                    "correlated_clusters": correlated,
                    "cross_space_coherence": coherence,
                    "member_document_ids": sorted(members),
                    "size": len(members),
                })

        return correlations

    def get_all_noise_doc_ids(
        self,
        space_doc_ids: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Return noise (outlier) document IDs per space."""
        result: dict[str, list[str]] = {}
        for space, clusterer in self._clusterers.items():
            doc_ids = space_doc_ids.get(space, [])
            noise_indices = clusterer.get_noise_indices()
            result[space] = [doc_ids[i] for i in noise_indices if i < len(doc_ids)]
        return result

    def get_all_centroids(
        self,
        space_vectors: dict[str, np.ndarray],
    ) -> dict[str, dict[int, np.ndarray]]:
        """Return cluster centroids for all spaces."""
        result: dict[str, dict[int, np.ndarray]] = {}
        for space, clusterer in self._clusterers.items():
            vectors = space_vectors.get(space)
            if vectors is not None:
                result[space] = clusterer.get_cluster_centroids(vectors)
        return result


# Legacy function for backward compatibility
def run_clustering(
    vectors: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: int = 3,
) -> tuple[np.ndarray, dict]:
    """Run HDBSCAN density-based clustering over embedding vectors.

    Returns:
        labels: array of cluster labels (-1 = noise)
        stats: clustering statistics
    """
    if vectors.shape[0] < min_cluster_size:
        return np.full(vectors.shape[0], -1), {
            "n_clusters": 0,
            "noise_count": vectors.shape[0],
            "noise_ratio": 1.0,
        }

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(vectors)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    noise_count = int(np.sum(labels == -1))

    return labels, {
        "n_clusters": n_clusters,
        "noise_count": noise_count,
        "noise_ratio": noise_count / len(labels) if len(labels) > 0 else 0,
        "probabilities": clusterer.probabilities_.tolist(),
    }
