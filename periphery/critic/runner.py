"""Critic sidecar runner — orchestrates scoring after each Crystallizer run.

Runs as a background process that:
  1. Watches for new Crystallizer snapshots
  2. Scores all structures in each snapshot
  3. Generates confidence explanations
  4. Propagates scores through the ontology
  5. Persists scoring results
  6. Triggers retraining when scheduled
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from periphery.critic.explanations import generate_explanation
from periphery.critic.network import CoherenceCritic
from periphery.critic.persistence import CriticStore
from periphery.critic.perturbations import PerturbationEngine
from periphery.critic.scoring import (
    ScoreCalibrator,
    SnapshotScorer,
    propagate_gradient_confidence,
    propagate_trajectory_confidence,
)
from periphery.critic.trainer import CriticTrainer
from periphery.crystallizer.models import (
    LivingOntologySnapshot,
)

logger = structlog.get_logger(__name__)


class CriticRunner:
    """Sidecar process that scores Crystallizer output.

    Coordinates the full critic pipeline: feature extraction, neural scoring,
    calibration, ensemble computation, propagation, and explanation generation.
    """

    def __init__(
        self,
        model: CoherenceCritic,
        trainer: CriticTrainer,
        store: CriticStore | None = None,
        device: str = "cpu",
        retraining_interval_runs: int = 10,
        retraining_interval_hours: float = 24.0,
        fine_tune_epochs: int = 20,
        perturbation_variants: int = 4,
        ensemble_weights: dict[str, float] | None = None,
        drift_mean_threshold: float = 0.15,
        drift_low_confidence_ratio: float = 0.5,
        drift_window_size: int = 5,
    ):
        self.model = model
        self.trainer = trainer
        self.store = store
        self.device = device

        self._retraining_interval_runs = retraining_interval_runs
        self._retraining_interval_hours = retraining_interval_hours
        self._fine_tune_epochs = fine_tune_epochs
        self._perturbation_variants = perturbation_variants

        self._perturbation_engine = PerturbationEngine()
        self._calibrator = ScoreCalibrator()
        self._scorer = SnapshotScorer(
            model=model,
            calibrator=self._calibrator,
            ensemble_weights=ensemble_weights,
            device=device,
        )

        self._runs_since_retrain = 0
        self._last_retrain_time: datetime | None = None
        self._last_scoring_results: list[dict[str, Any]] = []
        self._confidence_history: dict[str, list[float]] = {}
        self._bootstrapped: bool = False

        # Drift detection settings
        self._drift_mean_threshold = drift_mean_threshold
        self._drift_low_confidence_ratio = drift_low_confidence_ratio
        self._drift_window_size = drift_window_size
        self._drift_alerts: list[dict[str, Any]] = []

    async def load_state(self) -> None:
        """Load persisted confidence history and latest scores from DB."""
        if not self.store:
            return
        try:
            self._confidence_history = await self.store.load_confidence_history()
            latest = await self.store.get_latest_scores()
            if latest:
                self._last_scoring_results = [
                    {
                        "id": s.get("structure_id", ""),
                        "type": s.get("structure_type", ""),
                        "confidence": s.get("confidence", 0.0),
                        "confidence_raw": s.get("confidence_raw", 0.0),
                        "confidence_calibrated": s.get("confidence_calibrated", 0.0),
                        "signal_scores": s.get("signal_scores", {}),
                        "explanation": s.get("explanation", {}),
                    }
                    for s in latest
                ]
            logger.info(
                "critic_state_loaded",
                history_structures=len(self._confidence_history),
                latest_scores=len(self._last_scoring_results),
            )
        except Exception:
            logger.exception("critic_state_load_failed")

    async def score_snapshot(
        self, snapshot: LivingOntologySnapshot
    ) -> dict[str, Any]:
        """Score all structures in a Crystallizer snapshot.

        Returns a summary dict with scoring stats and the scored structures.
        """
        start_time = time.monotonic()

        # Bootstrap: train on first snapshot if no checkpoint exists
        if self.trainer.model_version == 0 and not self._bootstrapped:
            await self._bootstrap(snapshot)

        # Build structure dicts for scoring
        structures = self._snapshot_to_structures(snapshot)

        if not structures:
            return {
                "status": "skipped",
                "reason": "no_structures",
                "snapshot_id": snapshot.snapshot_id,
            }

        # Score all structures
        scored = self._scorer.score_structures(
            structures, corpus_size=snapshot.corpus_stats.total_documents
        )

        # Propagate scores
        scored = self._propagate_scores(scored, snapshot)

        # Generate explanations and update history
        changed_structures: dict[str, list[float]] = {}
        for s in scored:
            structure_id = s.get("id", "")
            history = self._confidence_history.get(structure_id)
            s["explanation"] = generate_explanation(s, history)

            # Update history
            if structure_id:
                if structure_id not in self._confidence_history:
                    self._confidence_history[structure_id] = []
                self._confidence_history[structure_id].append(s["confidence"])
                # Keep last 20 snapshots
                self._confidence_history[structure_id] = \
                    self._confidence_history[structure_id][-20:]
                changed_structures[structure_id] = self._confidence_history[structure_id]

        # Write confidence back to snapshot clusters
        self._apply_scores_to_snapshot(scored, snapshot)

        # Compute stats
        confidences = [s["confidence"] for s in scored]
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        stats = {
            "status": "scored",
            "snapshot_id": snapshot.snapshot_id,
            "structures_scored": len(scored),
            "mean_confidence": float(np.mean(confidences)) if confidences else 0.0,
            "median_confidence": float(np.median(confidences)) if confidences else 0.0,
            "low_confidence_count": sum(1 for c in confidences if c < 0.3),
            "high_confidence_count": sum(1 for c in confidences if c > 0.8),
            "scoring_time_ms": elapsed_ms,
        }

        # Persist run and scores
        run_id = None
        if self.store:
            run_id = f"cr_{uuid.uuid4().hex[:12]}"
            try:
                await self.store.save_run(
                    run_id=run_id,
                    model_version=self.trainer.model_version,
                    snapshot_id=snapshot.snapshot_id,
                    structures_scored=stats["structures_scored"],
                    mean_confidence=stats["mean_confidence"],
                    median_confidence=stats["median_confidence"],
                    low_confidence_count=stats["low_confidence_count"],
                    high_confidence_count=stats["high_confidence_count"],
                    scoring_time_ms=elapsed_ms,
                )
                await self.store.save_scores(run_id, scored)
            except Exception:
                logger.exception("critic_run_save_failed")

            # Persist changed confidence history
            try:
                if changed_structures:
                    await self.store.save_confidence_history(changed_structures)
            except Exception:
                logger.exception("critic_history_save_failed")

        self._last_scoring_results = scored
        self._runs_since_retrain += 1

        # Check for score drift
        self._check_drift(stats)

        logger.info(
            "critic_scoring_complete",
            snapshot_id=snapshot.snapshot_id,
            structures=len(scored),
            mean_confidence=f"{stats['mean_confidence']:.3f}",
            scoring_time_ms=elapsed_ms,
        )

        return stats

    async def _bootstrap(self, snapshot: LivingOntologySnapshot) -> None:
        """Train the model on the first snapshot when no checkpoint exists."""
        logger.info("critic_bootstrap_starting")
        samples = self._perturbation_engine.generate_dataset(
            clusters=snapshot.clusters,
            gradients=snapshot.relational_gradients,
            trajectories=snapshot.trajectories,
            variants_per_structure=self._perturbation_variants,
        )
        if not samples:
            self._bootstrapped = True
            return

        from periphery.config import get_settings
        settings = get_settings()
        result = self.trainer.train_on_samples(
            samples, epochs=settings.critic_initial_training_epochs
        )

        # Fit calibrator on validation data
        if result.get("status") == "trained" and self.trainer._last_val_data is not None:
            import torch
            X_val, y_val = self.trainer._last_val_data
            self.model.eval()
            with torch.no_grad():
                raw_preds = self.model(X_val).cpu().numpy()
            self.model.train()
            self._calibrator.fit(raw_preds, y_val.numpy())

        self.trainer.save_checkpoint(
            val_accuracy=result.get("final_val_accuracy", 0.0),
            dataset_size=len(samples),
            calibration_params=self._calibrator.get_params(),
        )
        self._bootstrapped = True
        logger.info(
            "critic_bootstrap_complete",
            status=result.get("status"),
            val_accuracy=result.get("final_val_accuracy"),
        )

    async def maybe_retrain(
        self, snapshot: LivingOntologySnapshot
    ) -> dict[str, Any] | None:
        """Check if retraining is needed and execute if so."""
        hours_since = float("inf")
        if self._last_retrain_time:
            elapsed = (datetime.now(timezone.utc) - self._last_retrain_time).total_seconds()
            hours_since = elapsed / 3600.0

        if not self.trainer.should_retrain(
            self._runs_since_retrain,
            hours_since,
            max_runs=self._retraining_interval_runs,
            max_hours=self._retraining_interval_hours,
        ):
            return None

        return await self.force_retrain(snapshot)

    async def force_retrain(
        self, snapshot: LivingOntologySnapshot
    ) -> dict[str, Any]:
        """Force retraining regardless of schedule."""
        logger.info("critic_retraining_triggered", runs_since=self._runs_since_retrain)

        # Generate new perturbation dataset from current snapshot
        samples = self._perturbation_engine.generate_dataset(
            clusters=snapshot.clusters,
            gradients=snapshot.relational_gradients,
            trajectories=snapshot.trajectories,
            variants_per_structure=self._perturbation_variants,
        )

        if not samples:
            return {"status": "skipped", "reason": "no_samples"}

        # Retrain with rollback protection, passing calibrator
        result = self.trainer.retrain_with_rollback(
            samples,
            fine_tune_epochs=self._fine_tune_epochs,
            calibrator=self._calibrator,
        )

        self._runs_since_retrain = 0
        self._last_retrain_time = datetime.now(timezone.utc)

        logger.info(
            "critic_retraining_complete",
            rolled_back=result.get("rolled_back", False),
            val_accuracy=result.get("final_val_accuracy"),
        )

        return result

    def _check_drift(self, current_stats: dict[str, Any]) -> None:
        """Check for score drift by comparing against recent runs."""
        if not self.store:
            return

        # Use in-memory data from last_scoring_results history
        mean_conf = current_stats.get("mean_confidence", 0.0)
        structures_scored = current_stats.get("structures_scored", 0)
        low_count = current_stats.get("low_confidence_count", 0)

        alerts = []

        # Check low-confidence ratio spike
        if structures_scored > 0:
            low_ratio = low_count / structures_scored
            if low_ratio >= self._drift_low_confidence_ratio:
                alerts.append({
                    "type": "low_confidence_ratio_spike",
                    "value": low_ratio,
                    "threshold": self._drift_low_confidence_ratio,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        # Store alerts
        if alerts:
            self._drift_alerts.extend(alerts)
            # Keep last 50 alerts
            self._drift_alerts = self._drift_alerts[-50:]
            logger.warning("critic_drift_detected", alerts=alerts)

    def _snapshot_to_structures(
        self, snapshot: LivingOntologySnapshot
    ) -> list[dict[str, Any]]:
        """Convert snapshot structures to scorer input format."""
        structures = []

        for cluster in snapshot.clusters:
            structures.append({
                "type": "cluster",
                "id": cluster.cluster_id,
                "features": {
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
                },
                "context": {
                    "num_sources": len(cluster.member_document_ids),
                    "cross_space_coherence": cluster.cross_space_coherence,
                    "age_snapshots": 1 if cluster.status == "forming" else 5,
                    "consistency_across_runs": cluster.stability,
                },
            })

        for gradient in snapshot.relational_gradients:
            structures.append({
                "type": "gradient",
                "id": f"{gradient.source_cluster}_{gradient.target_cluster}",
                "features": {
                    "gradient_score": gradient.gradient_score,
                    "entity_co_membership": gradient.components.entity_co_membership,
                    "temporal_alignment": gradient.components.temporal_alignment,
                    "geographic_proximity": gradient.components.geographic_proximity,
                    "relational_bridges": gradient.components.relational_bridges,
                    "bridge_entity_count": len(gradient.components.bridge_entities),
                    "gradient_trend": gradient.gradient_trend,
                },
                "context": {
                    "num_sources": gradient.components.relational_bridges,
                    "cross_space_coherence": 0.5,
                },
            })

        for trajectory in snapshot.trajectories:
            structures.append({
                "type": "trajectory",
                "id": trajectory.trajectory_id,
                "features": {
                    "velocity": trajectory.velocity,
                    "acceleration": trajectory.acceleration,
                    "confidence": trajectory.confidence,
                    "pattern": trajectory.pattern,
                    "snapshot_count": len(trajectory.snapshots),
                    "space": trajectory.space,
                },
                "context": {
                    "cross_space_coherence": 0.5,
                    "age_snapshots": len(trajectory.snapshots),
                },
            })

        return structures

    def _propagate_scores(
        self,
        scored: list[dict[str, Any]],
        snapshot: LivingOntologySnapshot,
    ) -> list[dict[str, Any]]:
        """Propagate confidence scores through the ontology."""
        # Build cluster confidence map
        cluster_conf: dict[str, float] = {}
        for s in scored:
            if s["type"] == "cluster":
                cluster_conf[s["id"]] = s["confidence"]

        # Propagate to gradients
        for s in scored:
            if s["type"] == "gradient":
                parts = s["id"].split("_", 1)
                if len(parts) == 2:
                    src_conf = cluster_conf.get(parts[0], 0.5)
                    tgt_conf = cluster_conf.get(parts[1], 0.5)
                else:
                    src_conf = tgt_conf = 0.5
                s["confidence"] = propagate_gradient_confidence(
                    s["confidence"], src_conf, tgt_conf
                )

        # Propagate to trajectories
        for s in scored:
            if s["type"] == "trajectory":
                # Find the cluster this trajectory belongs to
                traj_cluster = None
                for t in snapshot.trajectories:
                    if t.trajectory_id == s["id"]:
                        traj_cluster = t.cluster_id
                        break
                c_conf = cluster_conf.get(traj_cluster, 0.5) if traj_cluster else 0.5
                s["confidence"] = propagate_trajectory_confidence(
                    s["confidence"], c_conf
                )

        return scored

    def _apply_scores_to_snapshot(
        self,
        scored: list[dict[str, Any]],
        snapshot: LivingOntologySnapshot,
    ) -> None:
        """Write confidence scores back into the snapshot objects."""
        score_map: dict[str, float] = {}
        for s in scored:
            score_map[s["id"]] = s["confidence"]

        for cluster in snapshot.clusters:
            if cluster.cluster_id in score_map:
                cluster.confidence = score_map[cluster.cluster_id]

        for trajectory in snapshot.trajectories:
            if trajectory.trajectory_id in score_map:
                trajectory.confidence = score_map[trajectory.trajectory_id]

    @property
    def last_scoring_results(self) -> list[dict[str, Any]]:
        return self._last_scoring_results

    @property
    def runs_since_retrain(self) -> int:
        return self._runs_since_retrain

    @property
    def last_retrain_time(self) -> datetime | None:
        return self._last_retrain_time

    @property
    def drift_alerts(self) -> list[dict[str, Any]]:
        return self._drift_alerts

    def get_monitoring_stats(self) -> dict[str, Any]:
        """Return monitoring stats for the pipeline stats endpoint."""
        confidences = [s["confidence"] for s in self._last_scoring_results]

        # Build score histogram
        histogram = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}
        for c in confidences:
            if c < 0.2:
                histogram["0.0-0.2"] += 1
            elif c < 0.4:
                histogram["0.2-0.4"] += 1
            elif c < 0.6:
                histogram["0.4-0.6"] += 1
            elif c < 0.8:
                histogram["0.6-0.8"] += 1
            else:
                histogram["0.8-1.0"] += 1

        return {
            "model_version": self.trainer.model_version,
            "last_retrain_time": self._last_retrain_time.isoformat()
                if self._last_retrain_time else None,
            "runs_since_retrain": self._runs_since_retrain,
            "score_distribution": histogram,
            "low_confidence_alert_count": sum(1 for c in confidences if c < 0.3),
            "structures_scored": len(confidences),
            "mean_confidence": float(np.mean(confidences)) if confidences else 0.0,
            "retraining_status": "idle",
            "drift_alerts": self._drift_alerts[-10:],
        }
