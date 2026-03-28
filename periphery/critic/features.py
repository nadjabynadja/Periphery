"""Feature vector extraction for the CoherenceCritic.

Converts heterogeneous structures (clusters, relationships, gradients,
trajectories) into fixed-size numeric feature vectors with a structure-type
indicator prefix.

Feature vector layout:
  [type_indicator(4)] + [features(padded to max_dim)]

Type indicators:
  cluster:      [1, 0, 0, 0]
  relationship: [0, 1, 0, 0]
  gradient:     [0, 0, 1, 0]
  trajectory:   [0, 0, 0, 1]
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

# Feature dimensions per structure type (before padding)
CLUSTER_FEATURE_DIM = 16
RELATIONSHIP_FEATURE_DIM = 13
GRADIENT_FEATURE_DIM = 9
TRAJECTORY_FEATURE_DIM = 8

# Max feature dim (determines padding target)
MAX_FEATURE_DIM = CLUSTER_FEATURE_DIM  # 16
TYPE_PREFIX_DIM = 4
TOTAL_INPUT_DIM = TYPE_PREFIX_DIM + MAX_FEATURE_DIM  # 20

# Type indicator vectors
TYPE_CLUSTER = np.array([1, 0, 0, 0], dtype=np.float32)
TYPE_RELATIONSHIP = np.array([0, 1, 0, 0], dtype=np.float32)
TYPE_GRADIENT = np.array([0, 0, 1, 0], dtype=np.float32)
TYPE_TRAJECTORY = np.array([0, 0, 0, 1], dtype=np.float32)

# Encoding maps
STATUS_ENCODING = {
    "forming": 0.2,
    "stable": 0.8,
    "growing": 0.6,
    "shrinking": 0.4,
    "dissolved": 0.0,
}

TREND_ENCODING = {
    "weakening": -1.0,
    "stable": 0.0,
    "strengthening": 1.0,
}

PATTERN_ENCODING = {
    "stable": 0.0,
    "convergence": 0.5,
    "divergence": -0.5,
    "acceleration": 0.7,
    "emergence": 0.3,
}

SPACE_ENCODING = {
    "semantic": 0.2,
    "entity": 0.4,
    "relational": 0.6,
    "temporal": 0.8,
    "geospatial": 1.0,
}


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def extract_cluster_features(
    cluster_data: dict[str, Any],
    corpus_size: int = 1,
    snapshot_history: list[dict[str, Any]] | None = None,
) -> np.ndarray:
    """Extract a 16-dim feature vector from cluster data.

    Feature order:
        0: size_normalized (cluster size / corpus size)
        1: density (HDBSCAN cluster density)
        2: cross_space_coherence
        3: mean_source_credibility (normalized 0-1)
        4: credibility_variance
        5: temporal_span_days
        6: temporal_coherence
        7: geographic_coherence
        8: entity_diversity (unique entities / documents)
        9: relationship_density (relationships / documents)
        10: intra_cluster_similarity_mean
        11: intra_cluster_similarity_std
        12: centroid_stability
        13: member_churn_rate
        14: age_days
        15: growth_rate
    """
    size = cluster_data.get("size", 0)
    member_count = cluster_data.get("member_count", size)

    features = np.zeros(CLUSTER_FEATURE_DIM, dtype=np.float32)
    features[0] = _safe_div(size, max(corpus_size, 1))
    features[1] = cluster_data.get("density", 0.0)
    features[2] = cluster_data.get("cross_space_coherence", 0.0)
    features[3] = _normalize_credibility(cluster_data.get("mean_source_credibility", 4))
    features[4] = cluster_data.get("credibility_variance", 0.0)
    features[5] = _normalize_temporal_span(cluster_data.get("temporal_span_days", 0))
    features[6] = cluster_data.get("temporal_coherence", 0.5)
    features[7] = cluster_data.get("geographic_coherence", 0.5)
    features[8] = _safe_div(
        cluster_data.get("entity_count", 0),
        max(member_count, 1),
    )
    features[9] = _safe_div(
        cluster_data.get("relationship_count", 0),
        max(member_count, 1),
    )
    features[10] = cluster_data.get("intra_cluster_similarity_mean", 0.5)
    features[11] = cluster_data.get("intra_cluster_similarity_std", 0.1)
    features[12] = cluster_data.get("centroid_stability", 0.5)
    features[13] = cluster_data.get("member_churn_rate", 0.0)
    features[14] = _normalize_age(cluster_data.get("age_days", 0))
    features[15] = cluster_data.get("growth_rate", 0.0)

    # Use snapshot history for stability/churn if available
    if snapshot_history and len(snapshot_history) >= 2:
        latest = snapshot_history[-1]
        prev = snapshot_history[-2]
        size_change = latest.get("size", 0) - prev.get("size", 0)
        features[15] = _safe_div(size_change, max(prev.get("size", 1), 1))

    return features


def extract_relationship_features(
    rel_data: dict[str, Any],
) -> np.ndarray:
    """Extract a 13-dim feature vector from relationship data.

    Feature order:
        0: extraction_tier_max (normalized 0-1)
        1: num_sources (log-scaled)
        2: source_credibility_max (normalized)
        3: source_credibility_mean (normalized)
        4: temporal_consistency
        5: geographic_consistency
        6: co_occurrence_strength
        7: dependency_confidence
        8: llm_confidence
        9: predicate_frequency
        10: subject_cluster_membership (binary)
        11: object_cluster_membership (binary)
        12: cross_document_consistency
    """
    features = np.zeros(RELATIONSHIP_FEATURE_DIM, dtype=np.float32)
    features[0] = _normalize_tier(rel_data.get("extraction_tier_max", 3))
    features[1] = np.log1p(rel_data.get("num_sources", 1)) / 5.0
    features[2] = _normalize_credibility(rel_data.get("source_credibility_max", 4))
    features[3] = _normalize_credibility(rel_data.get("source_credibility_mean", 4))
    features[4] = rel_data.get("temporal_consistency", 0.5)
    features[5] = rel_data.get("geographic_consistency", 0.5)
    features[6] = rel_data.get("co_occurrence_strength", 0.0)
    features[7] = rel_data.get("dependency_confidence", 0.0)
    features[8] = rel_data.get("llm_confidence", 0.0)
    features[9] = rel_data.get("predicate_frequency", 0.5)
    features[10] = 1.0 if rel_data.get("subject_cluster_membership") else 0.0
    features[11] = 1.0 if rel_data.get("object_cluster_membership") else 0.0
    features[12] = rel_data.get("cross_document_consistency", 0.5)

    return features


def extract_gradient_features(
    gradient_data: dict[str, Any],
) -> np.ndarray:
    """Extract a 9-dim feature vector from gradient data.

    Feature order:
        0: entity_co_membership_score
        1: temporal_alignment_score
        2: geographic_proximity_score
        3: num_bridge_entities (log-scaled)
        4: bridge_entity_credibility_mean
        5: source_cluster_coherence
        6: target_cluster_coherence
        7: gradient_age_days (normalized)
        8: gradient_trend_numeric (-1, 0, 1)
    """
    features = np.zeros(GRADIENT_FEATURE_DIM, dtype=np.float32)
    features[0] = gradient_data.get("entity_co_membership", 0.0)
    features[1] = gradient_data.get("temporal_alignment", 0.0)
    features[2] = gradient_data.get("geographic_proximity", 0.0)
    features[3] = np.log1p(gradient_data.get("bridge_entity_count", 0)) / 3.0
    features[4] = _normalize_credibility(
        gradient_data.get("bridge_entity_credibility_mean", 4)
    )
    features[5] = gradient_data.get("source_cluster_coherence", 0.5)
    features[6] = gradient_data.get("target_cluster_coherence", 0.5)
    features[7] = _normalize_age(gradient_data.get("gradient_age_days", 0))
    features[8] = TREND_ENCODING.get(
        gradient_data.get("gradient_trend", "stable"), 0.0
    )

    return features


def extract_trajectory_features(
    traj_data: dict[str, Any],
) -> np.ndarray:
    """Extract an 8-dim feature vector from trajectory data.

    Feature order:
        0: velocity
        1: acceleration
        2: r_squared (fit quality)
        3: num_snapshots (log-scaled)
        4: cluster_coherence
        5: cluster_size (log-scaled)
        6: direction_consistency
        7: space_identifier_encoded
    """
    features = np.zeros(TRAJECTORY_FEATURE_DIM, dtype=np.float32)
    features[0] = min(traj_data.get("velocity", 0.0), 5.0) / 5.0
    features[1] = np.clip(traj_data.get("acceleration", 0.0), -2.0, 2.0) / 2.0
    features[2] = traj_data.get("r_squared", traj_data.get("confidence", 0.0))
    features[3] = np.log1p(traj_data.get("snapshot_count", 0)) / 4.0
    features[4] = traj_data.get("cluster_coherence", 0.5)
    features[5] = np.log1p(traj_data.get("cluster_size", 0)) / 8.0
    features[6] = traj_data.get("direction_consistency", 0.5)
    features[7] = SPACE_ENCODING.get(traj_data.get("space", "semantic"), 0.2)

    return features


def to_input_vector(
    structure_type: str,
    features: dict[str, Any],
    corpus_size: int = 1,
) -> np.ndarray:
    """Convert a structure to a fixed-size input vector for the Critic.

    Returns a vector of shape (TOTAL_INPUT_DIM,) with type prefix + padded features.
    """
    if structure_type == "cluster":
        type_prefix = TYPE_CLUSTER
        raw_features = extract_cluster_features(features, corpus_size)
    elif structure_type == "relationship":
        type_prefix = TYPE_RELATIONSHIP
        raw_features = extract_relationship_features(features)
    elif structure_type == "gradient":
        type_prefix = TYPE_GRADIENT
        raw_features = extract_gradient_features(features)
    elif structure_type == "trajectory":
        type_prefix = TYPE_TRAJECTORY
        raw_features = extract_trajectory_features(features)
    else:
        raise ValueError(f"Unknown structure type: {structure_type}")

    # Pad to MAX_FEATURE_DIM
    padded = np.zeros(MAX_FEATURE_DIM, dtype=np.float32)
    padded[: len(raw_features)] = raw_features

    return np.concatenate([type_prefix, padded])


# ── Normalization helpers ───────────────────────────────────────────────


def _normalize_credibility(tier: float) -> float:
    """Normalize credibility tier (1=best, 4=worst) to 0-1 (1=best)."""
    return max(0.0, min(1.0, (5 - tier) / 4.0))


def _normalize_tier(tier: float) -> float:
    """Normalize extraction tier (1-3) to 0-1."""
    return max(0.0, min(1.0, tier / 3.0))


def _normalize_temporal_span(days: float) -> float:
    """Normalize temporal span in days to 0-1 (365 days = 1.0)."""
    return min(1.0, days / 365.0)


def _normalize_age(days: float) -> float:
    """Normalize age in days to 0-1 (30 days = 1.0)."""
    return min(1.0, days / 30.0)
