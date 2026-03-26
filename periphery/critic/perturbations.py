"""Perturbation engine for generating synthetic corrupted structures.

Takes real Crystallizer output and generates plausible-but-wrong structural
variants. These serve as negative examples for training the CoherenceCritic.

Perturbation types:
  - entity_swap: replace entities with ones from different clusters
  - relationship_inversion: flip subject/object in relationships
  - temporal_scramble: randomize temporal contexts within a cluster
  - geographic_displacement: move geographic locations to wrong regions
  - density_inflation: add noise points to sparse regions
  - relationship_hallucination: fabricate unsupported relationships
  - cluster_merger: merge distinct clusters into one
  - gradient_fabrication: create spurious inter-cluster gradients

Each type is generated at subtle (10-20%), moderate (30-50%), and
obvious (70-100%) severity levels.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Optional

import numpy as np

from periphery.crystallizer.models import (
    DetectedCluster,
    GradientComponents,
    RelationalGradient,
    Trajectory,
    TrajectorySnapshot,
)


class PerturbationSample:
    """A single training sample: structure + label + metadata."""

    __slots__ = (
        "structure_type",
        "features",
        "is_perturbed",
        "perturbation_type",
        "perturbation_severity",
        "perturbation_details",
    )

    def __init__(
        self,
        structure_type: str,
        features: dict[str, Any],
        is_perturbed: bool,
        perturbation_type: Optional[str] = None,
        perturbation_severity: Optional[float] = None,
        perturbation_details: Optional[str] = None,
    ):
        self.structure_type = structure_type
        self.features = features
        self.is_perturbed = is_perturbed
        self.perturbation_type = perturbation_type
        self.perturbation_severity = perturbation_severity
        self.perturbation_details = perturbation_details

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure_type": self.structure_type,
            "features": self.features,
            "is_perturbed": self.is_perturbed,
            "perturbation_type": self.perturbation_type,
            "perturbation_severity": self.perturbation_severity,
            "perturbation_details": self.perturbation_details,
        }


# Severity bands
SUBTLE = (0.1, 0.2)
MODERATE = (0.3, 0.5)
OBVIOUS = (0.7, 1.0)
SEVERITY_LEVELS = {"subtle": SUBTLE, "moderate": MODERATE, "obvious": OBVIOUS}


class PerturbationEngine:
    """Generates perturbed structures from real Crystallizer output."""

    def __init__(self, rng: np.random.RandomState | None = None):
        self._rng = rng or np.random.RandomState()

    def generate_dataset(
        self,
        clusters: list[DetectedCluster],
        gradients: list[RelationalGradient],
        trajectories: list[Trajectory],
        variants_per_structure: int = 4,
    ) -> list[PerturbationSample]:
        """Generate a balanced dataset of real + perturbed samples.

        For each real structure, produces `variants_per_structure` perturbed
        versions across different perturbation types and severity levels.
        """
        samples: list[PerturbationSample] = []

        # Real cluster samples
        for cluster in clusters:
            features = self._cluster_features(cluster)
            samples.append(PerturbationSample(
                structure_type="cluster",
                features=features,
                is_perturbed=False,
            ))

            # Generate perturbed variants
            perturbations = self._select_cluster_perturbations(variants_per_structure)
            for ptype, severity_name in perturbations:
                severity = self._sample_severity(severity_name)
                perturbed = self._perturb_cluster(cluster, clusters, ptype, severity)
                if perturbed:
                    samples.append(perturbed)

        # Real gradient samples
        for gradient in gradients:
            features = self._gradient_features(gradient)
            samples.append(PerturbationSample(
                structure_type="gradient",
                features=features,
                is_perturbed=False,
            ))

            for _ in range(min(variants_per_structure, 2)):
                severity = self._sample_severity(
                    self._rng.choice(list(SEVERITY_LEVELS.keys()))
                )
                perturbed = self._perturb_gradient(gradient, gradients, clusters, severity)
                if perturbed:
                    samples.append(perturbed)

        # Real trajectory samples
        for trajectory in trajectories:
            features = self._trajectory_features(trajectory)
            samples.append(PerturbationSample(
                structure_type="trajectory",
                features=features,
                is_perturbed=False,
            ))

            for _ in range(min(variants_per_structure, 2)):
                severity = self._sample_severity(
                    self._rng.choice(list(SEVERITY_LEVELS.keys()))
                )
                perturbed = self._perturb_trajectory(trajectory, severity)
                if perturbed:
                    samples.append(perturbed)

        return samples

    # ── Feature extraction helpers ──────────────────────────────────────

    def _cluster_features(self, cluster: DetectedCluster) -> dict[str, Any]:
        """Extract raw feature dict from a cluster (for perturbation)."""
        return {
            "size": cluster.size,
            "density": cluster.density,
            "cross_space_coherence": cluster.cross_space_coherence,
            "stability": cluster.stability,
            "confidence": cluster.confidence,
            "member_count": len(cluster.member_document_ids),
            "entity_count": len(cluster.key_entities),
            "relationship_count": len(cluster.key_relationships),
            "status": cluster.status,
            "primary_space": cluster.primary_space,
            "has_geographic_center": cluster.geographic_center is not None,
            "has_temporal_center": cluster.temporal_center is not None,
        }

    def _gradient_features(self, gradient: RelationalGradient) -> dict[str, Any]:
        return {
            "gradient_score": gradient.gradient_score,
            "entity_co_membership": gradient.components.entity_co_membership,
            "temporal_alignment": gradient.components.temporal_alignment,
            "geographic_proximity": gradient.components.geographic_proximity,
            "relational_bridges": gradient.components.relational_bridges,
            "bridge_entity_count": len(gradient.components.bridge_entities),
            "gradient_trend": gradient.gradient_trend,
        }

    def _trajectory_features(self, trajectory: Trajectory) -> dict[str, Any]:
        return {
            "velocity": trajectory.velocity,
            "acceleration": trajectory.acceleration,
            "confidence": trajectory.confidence,
            "pattern": trajectory.pattern,
            "snapshot_count": len(trajectory.snapshots),
            "space": trajectory.space,
        }

    # ── Severity sampling ───────────────────────────────────────────────

    def _sample_severity(self, level: str) -> float:
        low, high = SEVERITY_LEVELS.get(level, MODERATE)
        return float(self._rng.uniform(low, high))

    def _select_cluster_perturbations(
        self, n: int
    ) -> list[tuple[str, str]]:
        """Select n perturbation (type, severity) pairs for a cluster."""
        types = [
            "entity_swap",
            "temporal_scramble",
            "geographic_displacement",
            "density_inflation",
            "cluster_merger",
        ]
        severities = list(SEVERITY_LEVELS.keys())

        selected = []
        for _ in range(n):
            ptype = self._rng.choice(types)
            severity = self._rng.choice(severities)
            selected.append((ptype, severity))
        return selected

    # ── Cluster perturbations ───────────────────────────────────────────

    def _perturb_cluster(
        self,
        cluster: DetectedCluster,
        all_clusters: list[DetectedCluster],
        perturbation_type: str,
        severity: float,
    ) -> Optional[PerturbationSample]:
        """Apply a specific perturbation to a cluster."""
        features = self._cluster_features(cluster)
        perturbed_features = copy.deepcopy(features)

        if perturbation_type == "entity_swap":
            return self._entity_swap(cluster, all_clusters, perturbed_features, severity)
        elif perturbation_type == "temporal_scramble":
            return self._temporal_scramble(perturbed_features, severity)
        elif perturbation_type == "geographic_displacement":
            return self._geographic_displacement(perturbed_features, severity)
        elif perturbation_type == "density_inflation":
            return self._density_inflation(perturbed_features, severity)
        elif perturbation_type == "cluster_merger":
            return self._cluster_merger(cluster, all_clusters, perturbed_features, severity)
        return None

    def _entity_swap(
        self,
        cluster: DetectedCluster,
        all_clusters: list[DetectedCluster],
        features: dict[str, Any],
        severity: float,
    ) -> PerturbationSample:
        """Replace some entities with ones from different clusters."""
        other_clusters = [c for c in all_clusters if c.cluster_id != cluster.cluster_id]
        n_swap = max(1, int(features["entity_count"] * severity))

        if other_clusters:
            donor = self._rng.choice(other_clusters)
            features["cross_space_coherence"] *= (1 - severity * 0.5)
            features["density"] *= (1 - severity * 0.3)

        features["entity_count"] = max(1, features["entity_count"])
        features["_swapped_entities"] = n_swap

        return PerturbationSample(
            structure_type="cluster",
            features=features,
            is_perturbed=True,
            perturbation_type="entity_swap",
            perturbation_severity=severity,
            perturbation_details=f"Swapped {n_swap} entities from foreign cluster",
        )

    def _temporal_scramble(
        self,
        features: dict[str, Any],
        severity: float,
    ) -> PerturbationSample:
        """Randomize temporal coherence signals."""
        features["has_temporal_center"] = self._rng.random() > 0.5
        features["stability"] *= (1 - severity * 0.6)
        features["cross_space_coherence"] *= (1 - severity * 0.4)

        return PerturbationSample(
            structure_type="cluster",
            features=features,
            is_perturbed=True,
            perturbation_type="temporal_scramble",
            perturbation_severity=severity,
            perturbation_details=f"Scrambled temporal context at {severity:.0%} severity",
        )

    def _geographic_displacement(
        self,
        features: dict[str, Any],
        severity: float,
    ) -> PerturbationSample:
        """Displace geographic coherence."""
        features["has_geographic_center"] = self._rng.random() > 0.7
        features["cross_space_coherence"] *= (1 - severity * 0.5)
        features["stability"] *= (1 - severity * 0.3)

        return PerturbationSample(
            structure_type="cluster",
            features=features,
            is_perturbed=True,
            perturbation_type="geographic_displacement",
            perturbation_severity=severity,
            perturbation_details=f"Displaced geographic locations at {severity:.0%} severity",
        )

    def _density_inflation(
        self,
        features: dict[str, Any],
        severity: float,
    ) -> PerturbationSample:
        """Inflate density with noise points."""
        noise_multiplier = 1 + severity * 3
        features["size"] = int(features["size"] * noise_multiplier)
        features["member_count"] = int(features["member_count"] * noise_multiplier)
        features["density"] *= (1 - severity * 0.7)
        features["cross_space_coherence"] *= (1 - severity * 0.6)
        features["entity_count"] = int(features["entity_count"] * (1 + severity * 0.5))

        return PerturbationSample(
            structure_type="cluster",
            features=features,
            is_perturbed=True,
            perturbation_type="density_inflation",
            perturbation_severity=severity,
            perturbation_details=f"Inflated density with {noise_multiplier:.1f}x noise points",
        )

    def _cluster_merger(
        self,
        cluster: DetectedCluster,
        all_clusters: list[DetectedCluster],
        features: dict[str, Any],
        severity: float,
    ) -> PerturbationSample:
        """Merge two distinct clusters."""
        other_clusters = [c for c in all_clusters if c.cluster_id != cluster.cluster_id]
        if not other_clusters:
            features["cross_space_coherence"] *= 0.5
        else:
            other = self._rng.choice(other_clusters)
            other_features = self._cluster_features(other)
            merge_fraction = severity
            features["size"] += int(other_features["size"] * merge_fraction)
            features["member_count"] += int(other_features["member_count"] * merge_fraction)
            features["density"] = (features["density"] + other_features["density"] * 0.3) / 2
            features["cross_space_coherence"] *= (1 - severity * 0.5)
            features["stability"] *= (1 - severity * 0.4)

        return PerturbationSample(
            structure_type="cluster",
            features=features,
            is_perturbed=True,
            perturbation_type="cluster_merger",
            perturbation_severity=severity,
            perturbation_details=f"Merged with foreign cluster at {severity:.0%} severity",
        )

    # ── Gradient perturbations ──────────────────────────────────────────

    def _perturb_gradient(
        self,
        gradient: RelationalGradient,
        all_gradients: list[RelationalGradient],
        all_clusters: list[DetectedCluster],
        severity: float,
    ) -> PerturbationSample:
        """Fabricate or corrupt a relational gradient."""
        features = self._gradient_features(gradient)
        perturbed = copy.deepcopy(features)

        perturbation_type = self._rng.choice([
            "gradient_fabrication",
            "relationship_inversion",
            "relationship_hallucination",
        ])

        if perturbation_type == "gradient_fabrication":
            perturbed["entity_co_membership"] *= (1 - severity * 0.8)
            perturbed["temporal_alignment"] = float(self._rng.uniform(0, 0.3))
            perturbed["geographic_proximity"] = float(self._rng.uniform(0, 0.2))
            perturbed["relational_bridges"] = max(0, int(perturbed["relational_bridges"] * (1 - severity)))
            perturbed["bridge_entity_count"] = max(0, int(perturbed["bridge_entity_count"] * (1 - severity)))
            detail = f"Fabricated gradient connections at {severity:.0%} severity"

        elif perturbation_type == "relationship_inversion":
            perturbed["temporal_alignment"] *= (1 - severity * 0.5)
            perturbed["gradient_score"] *= (1 - severity * 0.4)
            detail = f"Inverted relationship directionality at {severity:.0%} severity"

        else:  # relationship_hallucination
            perturbed["entity_co_membership"] = float(self._rng.uniform(0, 0.2))
            perturbed["gradient_score"] = float(self._rng.uniform(0.1, 0.4))
            perturbed["relational_bridges"] = 0
            perturbed["bridge_entity_count"] = 0
            detail = f"Hallucinated relationship with no geometric support"

        return PerturbationSample(
            structure_type="gradient",
            features=perturbed,
            is_perturbed=True,
            perturbation_type=perturbation_type,
            perturbation_severity=severity,
            perturbation_details=detail,
        )

    # ── Trajectory perturbations ────────────────────────────────────────

    def _perturb_trajectory(
        self,
        trajectory: Trajectory,
        severity: float,
    ) -> PerturbationSample:
        """Corrupt a trajectory's motion parameters."""
        features = self._trajectory_features(trajectory)
        perturbed = copy.deepcopy(features)

        perturbed["velocity"] *= float(self._rng.uniform(1 + severity, 1 + severity * 5))
        perturbed["acceleration"] = float(
            self._rng.uniform(-severity * 2, severity * 2)
        )
        perturbed["confidence"] *= (1 - severity * 0.6)
        perturbed["snapshot_count"] = max(2, int(perturbed["snapshot_count"] * (1 - severity * 0.5)))

        return PerturbationSample(
            structure_type="trajectory",
            features=perturbed,
            is_perturbed=True,
            perturbation_type="trajectory_corruption",
            perturbation_severity=severity,
            perturbation_details=f"Corrupted trajectory motion at {severity:.0%} severity",
        )
