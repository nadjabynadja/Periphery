"""Scoring pipeline — calibration, propagation, and multi-signal ensemble.

Scores every structure in a Crystallizer snapshot, calibrates raw scores,
propagates confidence through the ontology graph, and computes the
multi-signal ensemble.

Score flow:
  1. Extract feature vectors for all structures
  2. Run forward passes through the CoherenceCritic
  3. Calibrate raw sigmoid outputs via Platt scaling / isotonic regression
  4. Compute independent quality signals (source diversity, temporal, etc.)
  5. Combine into weighted ensemble
  6. Propagate through ontology (entity, relationship, gradient, trajectory)
  7. Generate confidence explanations
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import torch

from periphery.critic.features import to_input_vector
from periphery.critic.network import CoherenceCritic

logger = logging.getLogger(__name__)


# ── Ensemble weights (configurable) ────────────────────────────────────

DEFAULT_ENSEMBLE_WEIGHTS = {
    "critic_neural": 0.4,
    "source_diversity": 0.15,
    "temporal_consistency": 0.15,
    "cross_space_agreement": 0.15,
    "stability": 0.15,
}


# ── Score calibration ──────────────────────────────────────────────────


class ScoreCalibrator:
    """Calibrates raw Critic scores to well-calibrated confidence values.

    Uses isotonic regression (preferred) or Platt scaling on validation data.
    After calibration, 0.9+ means high confidence, 0.5-0.7 plausible but
    uncertain, below 0.3 likely noise.
    """

    def __init__(self):
        self._calibrator = None
        self._method: str = "none"

    def fit(self, raw_scores: np.ndarray, true_labels: np.ndarray) -> None:
        """Fit calibration on validation data."""
        try:
            from sklearn.isotonic import IsotonicRegression

            self._calibrator = IsotonicRegression(
                y_min=0.0, y_max=1.0, out_of_bounds="clip"
            )
            self._calibrator.fit(raw_scores, true_labels)
            self._method = "isotonic"
        except ImportError:
            # Fallback: linear rescaling
            self._method = "none"
            logger.warning("sklearn not available, using uncalibrated scores")

    def calibrate(self, raw_scores: np.ndarray) -> np.ndarray:
        """Apply calibration to raw scores."""
        if self._calibrator is not None:
            return self._calibrator.predict(raw_scores)
        return raw_scores

    def calibrate_single(self, raw_score: float) -> float:
        """Calibrate a single score."""
        arr = np.array([raw_score])
        return float(self.calibrate(arr)[0])

    def get_params(self) -> dict[str, Any]:
        """Get serializable calibration parameters."""
        if self._method == "isotonic" and self._calibrator is not None:
            return {
                "method": "isotonic",
                "X_thresholds": self._calibrator.X_thresholds_.tolist()
                if hasattr(self._calibrator, "X_thresholds_")
                else [],
                "y_thresholds": self._calibrator.y_thresholds_.tolist()
                if hasattr(self._calibrator, "y_thresholds_")
                else [],
            }
        return {"method": self._method}

    def load_params(self, params: dict[str, Any]) -> None:
        """Load calibration parameters."""
        method = params.get("method", "none")
        if method == "isotonic":
            try:
                from sklearn.isotonic import IsotonicRegression

                self._calibrator = IsotonicRegression(
                    y_min=0.0, y_max=1.0, out_of_bounds="clip"
                )
                if params.get("X_thresholds") and params.get("y_thresholds"):
                    X = np.array(params["X_thresholds"])
                    y = np.array(params["y_thresholds"])
                    self._calibrator.fit(X, y)
                self._method = "isotonic"
            except ImportError:
                self._method = "none"
        else:
            self._method = "none"


# ── Multi-signal scoring ───────────────────────────────────────────────


def compute_source_diversity(num_unique_sources: int) -> float:
    """Score based on source independence. More sources = more coherent."""
    if num_unique_sources <= 0:
        return 0.0
    return 1.0 - (1.0 / num_unique_sources)


def compute_temporal_consistency(
    temporal_tags: list[str] | None = None,
    temporal_conflicts: int = 0,
    total_temporal: int = 1,
) -> float:
    """Score based on temporal tag agreement within a structure."""
    if total_temporal <= 0:
        return 0.5
    if temporal_tags:
        unique = len(set(temporal_tags))
        return 1.0 - (unique - 1) / max(len(temporal_tags), 1)
    return max(0.0, 1.0 - temporal_conflicts / total_temporal)


def compute_stability_score(
    age_snapshots: int = 0,
    consistency_across_runs: float = 1.0,
) -> float:
    """Score structures that persist across Crystallizer runs."""
    age_factor = min(1.0, age_snapshots / 10.0)
    return age_factor * consistency_across_runs


def compute_ensemble_score(
    critic_neural: float,
    source_diversity: float,
    temporal_consistency: float,
    cross_space_agreement: float,
    stability: float,
    weights: dict[str, float] | None = None,
) -> float:
    """Combine all signals into a final confidence score."""
    w = weights or DEFAULT_ENSEMBLE_WEIGHTS
    return (
        w.get("critic_neural", 0.4) * critic_neural
        + w.get("source_diversity", 0.15) * source_diversity
        + w.get("temporal_consistency", 0.15) * temporal_consistency
        + w.get("cross_space_agreement", 0.15) * cross_space_agreement
        + w.get("stability", 0.15) * stability
    )


# ── Score propagation ──────────────────────────────────────────────────


def propagated_confidence(
    critic_score: float,
    context_scores: list[float],
    context_weight: float = 0.3,
) -> float:
    """Blend the Critic's direct score with contextual confidence.

    context_scores: confidence scores from related structures.
    context_weight: how much context influences the final score.
    """
    context_mean = (
        sum(context_scores) / len(context_scores) if context_scores else 0.5
    )
    return (1 - context_weight) * critic_score + context_weight * context_mean


def propagate_entity_confidence(
    cluster_confidence: float,
    entity_centrality: float = 0.5,
) -> float:
    """Derive entity confidence from cluster confidence and centrality."""
    return cluster_confidence * (0.5 + 0.5 * entity_centrality)


def propagate_relationship_confidence(
    relationship_critic_score: float,
    subject_confidence: float,
    object_confidence: float,
    context_weight: float = 0.3,
) -> float:
    """Derive relationship confidence from entities and critic score."""
    entity_mean = (subject_confidence + object_confidence) / 2.0
    return propagated_confidence(
        relationship_critic_score, [entity_mean], context_weight
    )


def propagate_trajectory_confidence(
    trajectory_critic_score: float,
    cluster_confidence: float,
    context_weight: float = 0.3,
) -> float:
    """Derive trajectory confidence from cluster confidence."""
    return propagated_confidence(
        trajectory_critic_score, [cluster_confidence], context_weight
    )


def propagate_gradient_confidence(
    gradient_critic_score: float,
    source_cluster_confidence: float,
    target_cluster_confidence: float,
    context_weight: float = 0.3,
) -> float:
    """Derive gradient confidence from both clusters."""
    return propagated_confidence(
        gradient_critic_score,
        [source_cluster_confidence, target_cluster_confidence],
        context_weight,
    )


# ── Batch scoring ──────────────────────────────────────────────────────


class SnapshotScorer:
    """Scores all structures in a Crystallizer snapshot."""

    def __init__(
        self,
        model: CoherenceCritic,
        calibrator: ScoreCalibrator | None = None,
        ensemble_weights: dict[str, float] | None = None,
        device: str = "cpu",
    ):
        self.model = model
        self.calibrator = calibrator or ScoreCalibrator()
        self.ensemble_weights = ensemble_weights or DEFAULT_ENSEMBLE_WEIGHTS
        self.device = device

    def score_structures(
        self,
        structures: list[dict[str, Any]],
        corpus_size: int = 1,
    ) -> list[dict[str, Any]]:
        """Score a batch of structures.

        Each structure dict must have:
          - "type": one of "cluster", "relationship", "gradient", "trajectory"
          - "features": dict of feature values
          - Optional: "context" dict with source_count, temporal_tags, etc.

        Returns list of dicts with scores added.
        """
        if not structures:
            return []

        # Build input tensors
        input_vectors = []
        for s in structures:
            vec = to_input_vector(s["type"], s["features"], corpus_size)
            input_vectors.append(vec)

        X = torch.tensor(np.array(input_vectors), dtype=torch.float32).to(self.device)

        # Forward pass
        self.model.eval()
        with torch.no_grad():
            raw_scores = self.model(X).cpu().numpy()

        # Calibrate
        calibrated = self.calibrator.calibrate(raw_scores)

        # Compute ensemble for each structure
        results = []
        for i, s in enumerate(structures):
            ctx = s.get("context", {})

            source_div = compute_source_diversity(ctx.get("num_sources", 1))
            temporal_cons = compute_temporal_consistency(
                ctx.get("temporal_tags"),
                ctx.get("temporal_conflicts", 0),
                ctx.get("total_temporal", 1),
            )
            cross_space = ctx.get("cross_space_coherence", 0.5)
            stability = compute_stability_score(
                ctx.get("age_snapshots", 0),
                ctx.get("consistency_across_runs", 1.0),
            )

            ensemble = compute_ensemble_score(
                critic_neural=float(calibrated[i]),
                source_diversity=source_div,
                temporal_consistency=temporal_cons,
                cross_space_agreement=cross_space,
                stability=stability,
                weights=self.ensemble_weights,
            )

            result = {
                **s,
                "confidence_raw": float(raw_scores[i]),
                "confidence_calibrated": float(calibrated[i]),
                "confidence": ensemble,
                "signal_scores": {
                    "critic_neural": float(calibrated[i]),
                    "source_diversity": source_div,
                    "temporal_consistency": temporal_cons,
                    "cross_space_agreement": cross_space,
                    "stability": stability,
                },
            }
            results.append(result)

        return results
