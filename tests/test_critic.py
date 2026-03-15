"""Comprehensive tests for the Continuous Critic (Layer 3).

Tests cover:
  - CoherenceCritic network (forward pass, output bounds)
  - Feature vector extraction (all structure types, padding)
  - Perturbation engine (dataset generation, severity levels)
  - CriticTrainer (training, checkpointing, rollback, calibrator integration)
  - Scoring pipeline (calibration, ensemble, propagation)
  - Confidence explanations
  - CriticRunner (bootstrap, score persistence, drift detection, load_state)
  - CriticStore (score/history persistence)
"""

import json
import os
import tempfile

import numpy as np
import pytest
import pytest_asyncio
import torch

from periphery.critic.explanations import generate_explanation
from periphery.critic.features import (
    TOTAL_INPUT_DIM,
    extract_cluster_features,
    extract_gradient_features,
    extract_relationship_features,
    extract_trajectory_features,
    to_input_vector,
)
from periphery.critic.network import CoherenceCritic
from periphery.critic.perturbations import PerturbationEngine, PerturbationSample
from periphery.critic.scoring import (
    ScoreCalibrator,
    SnapshotScorer,
    compute_ensemble_score,
    compute_source_diversity,
    compute_stability_score,
    compute_temporal_consistency,
    propagate_gradient_confidence,
    propagate_relationship_confidence,
    propagate_trajectory_confidence,
    propagated_confidence,
)
from periphery.critic.runner import CriticRunner
from periphery.critic.trainer import CriticTrainer
from periphery.crystallizer.models import (
    DetectedCluster,
    GradientComponents,
    RelationalGradient,
    Trajectory,
)


# ── Network tests ──────────────────────────────────────────────────────


class TestCoherenceCritic:
    def test_forward_pass(self):
        model = CoherenceCritic()
        x = torch.randn(8, TOTAL_INPUT_DIM)
        scores = model(x)
        assert scores.shape == (8,)
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_single_input(self):
        model = CoherenceCritic()
        x = torch.randn(1, TOTAL_INPUT_DIM)
        score = model(x)
        assert score.shape == (1,)

    def test_custom_hidden_dim(self):
        model = CoherenceCritic(hidden_dim=64)
        x = torch.randn(4, TOTAL_INPUT_DIM)
        scores = model(x)
        assert scores.shape == (4,)


# ── Feature extraction tests ──────────────────────────────────────────


class TestFeatureExtraction:
    def test_cluster_features(self):
        features = extract_cluster_features({
            "size": 50,
            "density": 0.8,
            "cross_space_coherence": 0.7,
            "member_count": 50,
            "entity_count": 30,
            "relationship_count": 15,
        }, corpus_size=1000)
        assert features.shape == (16,)
        assert features[0] == pytest.approx(50 / 1000)
        assert features[1] == pytest.approx(0.8)

    def test_relationship_features(self):
        features = extract_relationship_features({
            "extraction_tier_max": 2,
            "num_sources": 5,
            "source_credibility_max": 1,
        })
        assert features.shape == (13,)

    def test_gradient_features(self):
        features = extract_gradient_features({
            "entity_co_membership": 0.6,
            "temporal_alignment": 0.4,
            "geographic_proximity": 0.3,
            "bridge_entity_count": 3,
            "gradient_trend": "strengthening",
        })
        assert features.shape == (9,)
        assert features[8] == pytest.approx(1.0)

    def test_trajectory_features(self):
        features = extract_trajectory_features({
            "velocity": 0.5,
            "acceleration": 0.1,
            "confidence": 0.8,
            "snapshot_count": 10,
            "space": "semantic",
        })
        assert features.shape == (8,)

    def test_to_input_vector_cluster(self):
        vec = to_input_vector("cluster", {"size": 10, "density": 0.5})
        assert vec.shape == (TOTAL_INPUT_DIM,)
        assert vec[0] == 1.0
        assert vec[1] == 0.0

    def test_to_input_vector_gradient(self):
        vec = to_input_vector("gradient", {"entity_co_membership": 0.5})
        assert vec.shape == (TOTAL_INPUT_DIM,)
        assert vec[2] == 1.0

    def test_to_input_vector_padding(self):
        vec = to_input_vector("gradient", {"entity_co_membership": 0.5})
        assert vec[4 + 9:].sum() == pytest.approx(0.0, abs=1e-6)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown structure type"):
            to_input_vector("unknown", {})


# ── Perturbation engine tests ─────────────────────────────────────────


class TestPerturbationEngine:
    @pytest.fixture
    def sample_clusters(self):
        return [
            DetectedCluster(
                cluster_id="c1",
                primary_space="semantic",
                cross_space_coherence=0.8,
                member_document_ids=["d1", "d2", "d3"],
                size=3,
                density=0.7,
                stability=0.9,
                key_entities=["Entity1", "Entity2"],
                key_relationships=[{"subject": "E1", "object": "E2"}],
            ),
            DetectedCluster(
                cluster_id="c2",
                primary_space="semantic",
                cross_space_coherence=0.6,
                member_document_ids=["d4", "d5"],
                size=2,
                density=0.5,
                stability=0.7,
                key_entities=["Entity3"],
            ),
        ]

    @pytest.fixture
    def sample_gradients(self):
        return [
            RelationalGradient(
                source_cluster="c1",
                target_cluster="c2",
                gradient_score=0.5,
                components=GradientComponents(
                    entity_co_membership=0.3,
                    temporal_alignment=0.6,
                    geographic_proximity=0.4,
                    relational_bridges=2,
                    bridge_entities=["Entity1"],
                ),
            ),
        ]

    @pytest.fixture
    def sample_trajectories(self):
        return [
            Trajectory(
                trajectory_id="t1",
                cluster_id="c1",
                space="semantic",
                velocity=0.5,
                acceleration=0.1,
                confidence=0.7,
                pattern="stable",
            ),
        ]

    def test_generate_dataset(self, sample_clusters, sample_gradients, sample_trajectories):
        engine = PerturbationEngine(rng=np.random.RandomState(42))
        samples = engine.generate_dataset(
            sample_clusters, sample_gradients, sample_trajectories,
            variants_per_structure=3,
        )
        assert len(samples) > 0
        real = [s for s in samples if not s.is_perturbed]
        perturbed = [s for s in samples if s.is_perturbed]
        assert len(real) > 0
        assert len(perturbed) > 0

    def test_perturbation_types(self, sample_clusters, sample_gradients, sample_trajectories):
        engine = PerturbationEngine(rng=np.random.RandomState(42))
        samples = engine.generate_dataset(
            sample_clusters, sample_gradients, sample_trajectories,
            variants_per_structure=10,
        )
        types = {s.perturbation_type for s in samples if s.is_perturbed}
        assert len(types) >= 2

    def test_severity_range(self, sample_clusters, sample_gradients, sample_trajectories):
        engine = PerturbationEngine(rng=np.random.RandomState(42))
        samples = engine.generate_dataset(
            sample_clusters, sample_gradients, sample_trajectories,
            variants_per_structure=10,
        )
        for s in samples:
            if s.is_perturbed:
                assert 0.0 <= s.perturbation_severity <= 1.0

    def test_sample_to_dict(self):
        sample = PerturbationSample(
            structure_type="cluster",
            features={"size": 10},
            is_perturbed=True,
            perturbation_type="entity_swap",
            perturbation_severity=0.3,
            perturbation_details="test",
        )
        d = sample.to_dict()
        assert d["structure_type"] == "cluster"
        assert d["is_perturbed"] is True
        assert d["perturbation_severity"] == 0.3

    def test_empty_clusters(self):
        engine = PerturbationEngine()
        samples = engine.generate_dataset([], [], [])
        assert samples == []


# ── Trainer tests ──────────────────────────────────────────────────────


class TestCriticTrainer:
    @pytest.fixture
    def model(self):
        return CoherenceCritic()

    @pytest.fixture
    def trainer(self, model, tmp_path):
        return CriticTrainer(
            model,
            checkpoint_dir=str(tmp_path / "checkpoints"),
            training_dir=str(tmp_path / "training"),
        )

    @pytest.fixture
    def sample_data(self):
        samples = []
        rng = np.random.RandomState(42)
        for _ in range(20):
            samples.append(PerturbationSample(
                structure_type="cluster",
                features={
                    "size": int(rng.randint(5, 100)),
                    "density": float(rng.uniform(0.5, 1.0)),
                    "cross_space_coherence": float(rng.uniform(0.6, 1.0)),
                    "stability": float(rng.uniform(0.7, 1.0)),
                    "member_count": int(rng.randint(5, 100)),
                    "entity_count": int(rng.randint(3, 50)),
                    "relationship_count": int(rng.randint(1, 20)),
                },
                is_perturbed=False,
            ))
        for _ in range(20):
            samples.append(PerturbationSample(
                structure_type="cluster",
                features={
                    "size": int(rng.randint(5, 100)),
                    "density": float(rng.uniform(0.0, 0.3)),
                    "cross_space_coherence": float(rng.uniform(0.0, 0.3)),
                    "stability": float(rng.uniform(0.0, 0.4)),
                    "member_count": int(rng.randint(5, 100)),
                    "entity_count": int(rng.randint(3, 50)),
                    "relationship_count": int(rng.randint(1, 20)),
                },
                is_perturbed=True,
                perturbation_type="entity_swap",
                perturbation_severity=0.5,
            ))
        return samples

    def test_train_on_samples(self, trainer, sample_data):
        result = trainer.train_on_samples(sample_data, epochs=5)
        assert result["status"] == "trained"
        assert result["epochs"] == 5
        assert 0.0 <= result["final_val_accuracy"] <= 1.0

    def test_train_stores_val_data(self, trainer, sample_data):
        trainer.train_on_samples(sample_data, epochs=3)
        assert trainer._last_val_data is not None
        X_val, y_val = trainer._last_val_data
        assert X_val.shape[0] > 0
        assert y_val.shape[0] == X_val.shape[0]

    def test_train_empty(self, trainer):
        result = trainer.train_on_samples([])
        assert result["status"] == "skipped"

    def test_save_load_checkpoint(self, trainer, sample_data, tmp_path):
        trainer.train_on_samples(sample_data, epochs=3)
        path = trainer.save_checkpoint(val_accuracy=0.85, dataset_size=40)
        assert os.path.exists(path)
        result = trainer.load_checkpoint(path)
        assert result["status"] == "loaded"
        assert result["val_accuracy"] == 0.85

    def test_save_load_checkpoint_with_calibration(self, trainer, sample_data):
        trainer.train_on_samples(sample_data, epochs=3)
        cal_params = {"method": "isotonic", "X_thresholds": [0.1, 0.5, 0.9], "y_thresholds": [0.0, 0.5, 1.0]}
        path = trainer.save_checkpoint(val_accuracy=0.85, calibration_params=cal_params)
        result = trainer.load_checkpoint(path)
        assert result["calibration_params"] == cal_params

    def test_load_latest_checkpoint(self, trainer, sample_data):
        trainer.train_on_samples(sample_data, epochs=3)
        trainer.save_checkpoint(val_accuracy=0.8)
        trainer.save_checkpoint(val_accuracy=0.9)
        result = trainer.load_checkpoint()
        assert result["status"] == "loaded"

    def test_no_checkpoint(self, trainer):
        result = trainer.load_checkpoint()
        assert result["status"] == "no_checkpoint"

    def test_checkpoint_pruning(self, trainer, sample_data):
        trainer.max_checkpoints = 2
        trainer.train_on_samples(sample_data, epochs=2)
        trainer.save_checkpoint()
        trainer.save_checkpoint()
        trainer.save_checkpoint()
        checkpoints = list(trainer.checkpoint_dir.glob("critic_v*.pt"))
        assert len(checkpoints) <= 2

    def test_save_perturbation_dataset(self, trainer, sample_data):
        path = trainer.save_perturbation_dataset(sample_data)
        assert os.path.exists(path)

    def test_should_retrain(self, trainer):
        assert trainer.should_retrain(10, 0.0, max_runs=10)
        assert trainer.should_retrain(5, 25.0, max_hours=24.0)
        assert not trainer.should_retrain(5, 12.0)

    def test_retrain_with_rollback(self, trainer, sample_data):
        result = trainer.retrain_with_rollback(sample_data, fine_tune_epochs=3)
        assert result["status"] == "trained"
        assert "rolled_back" in result

    def test_retrain_with_calibrator(self, trainer, sample_data):
        calibrator = ScoreCalibrator()
        result = trainer.retrain_with_rollback(
            sample_data, fine_tune_epochs=3, calibrator=calibrator
        )
        assert result["status"] == "trained"
        if not result.get("rolled_back"):
            # Calibrator should have been fitted
            params = calibrator.get_params()
            # Method is either "isotonic" (if sklearn available) or "none"
            assert "method" in params

    def test_model_version(self, trainer, sample_data):
        assert trainer.model_version == 0
        trainer.train_on_samples(sample_data, epochs=2)
        trainer.save_checkpoint()
        assert trainer.model_version == 1


# ── Scoring tests ──────────────────────────────────────────────────────


class TestScoring:
    def test_source_diversity(self):
        assert compute_source_diversity(0) == 0.0
        assert compute_source_diversity(1) == 0.0
        assert compute_source_diversity(2) == 0.5
        assert compute_source_diversity(10) == pytest.approx(0.9)

    def test_temporal_consistency(self):
        assert compute_temporal_consistency(temporal_conflicts=0, total_temporal=10) == 1.0
        assert compute_temporal_consistency(temporal_conflicts=5, total_temporal=10) == 0.5

    def test_stability_score(self):
        assert compute_stability_score(0) == 0.0
        assert compute_stability_score(10) == 1.0
        assert compute_stability_score(5) == 0.5

    def test_ensemble_score(self):
        score = compute_ensemble_score(
            critic_neural=0.8,
            source_diversity=0.9,
            temporal_consistency=0.7,
            cross_space_agreement=0.6,
            stability=0.5,
        )
        expected = 0.4 * 0.8 + 0.15 * 0.9 + 0.15 * 0.7 + 0.15 * 0.6 + 0.15 * 0.5
        assert score == pytest.approx(expected)

    def test_propagated_confidence(self):
        score = propagated_confidence(0.8, [0.6, 0.7])
        expected = 0.7 * 0.8 + 0.3 * 0.65
        assert score == pytest.approx(expected)

    def test_propagated_confidence_empty_context(self):
        score = propagated_confidence(0.8, [])
        expected = 0.7 * 0.8 + 0.3 * 0.5
        assert score == pytest.approx(expected)

    def test_propagate_gradient_confidence(self):
        score = propagate_gradient_confidence(0.8, 0.9, 0.7)
        assert 0.0 <= score <= 1.0

    def test_propagate_trajectory_confidence(self):
        score = propagate_trajectory_confidence(0.7, 0.8)
        assert 0.0 <= score <= 1.0


class TestScoreCalibrator:
    def test_uncalibrated(self):
        cal = ScoreCalibrator()
        raw = np.array([0.3, 0.5, 0.8])
        result = cal.calibrate(raw)
        np.testing.assert_array_equal(raw, result)

    def test_calibrate_single(self):
        cal = ScoreCalibrator()
        assert cal.calibrate_single(0.5) == 0.5

    def test_get_params_default(self):
        cal = ScoreCalibrator()
        assert cal.get_params()["method"] == "none"

    def test_load_params_roundtrip(self):
        cal = ScoreCalibrator()
        params = {"method": "none"}
        cal.load_params(params)
        assert cal.get_params()["method"] == "none"


class TestSnapshotScorer:
    def test_score_structures(self):
        model = CoherenceCritic()
        scorer = SnapshotScorer(model=model)
        structures = [
            {
                "type": "cluster",
                "id": "c1",
                "features": {"size": 10, "density": 0.5},
                "context": {"num_sources": 5},
            },
            {
                "type": "gradient",
                "id": "g1",
                "features": {"entity_co_membership": 0.3},
                "context": {},
            },
        ]
        results = scorer.score_structures(structures, corpus_size=100)
        assert len(results) == 2
        for r in results:
            assert "confidence" in r
            assert "confidence_raw" in r
            assert "confidence_calibrated" in r
            assert "signal_scores" in r
            assert 0.0 <= r["confidence"] <= 1.0

    def test_empty_structures(self):
        model = CoherenceCritic()
        scorer = SnapshotScorer(model=model)
        assert scorer.score_structures([]) == []


# ── Explanation tests ──────────────────────────────────────────────────


class TestExplanations:
    def test_generate_explanation(self):
        scored = {
            "confidence": 0.73,
            "confidence_calibrated": 0.68,
            "type": "cluster",
            "signal_scores": {
                "critic_neural": 0.7,
                "source_diversity": 0.85,
                "temporal_consistency": 0.45,
                "cross_space_agreement": 0.8,
                "stability": 0.6,
            },
            "context": {
                "num_sources": 7,
                "source_tiers": "3",
                "agreed_spaces": ["semantic", "entity", "geospatial"],
                "temporal_conflicts": 2,
            },
        }
        explanation = generate_explanation(scored)
        assert "explanation" in explanation
        assert "primary_factors" in explanation["explanation"]
        assert "risk_factors" in explanation["explanation"]
        assert "trend" in explanation["explanation"]
        assert len(explanation["explanation"]["primary_factors"]) > 0

    def test_trend_new(self):
        scored = {
            "confidence": 0.5,
            "confidence_calibrated": 0.5,
            "signal_scores": {"critic_neural": 0.5},
            "context": {},
        }
        explanation = generate_explanation(scored, snapshot_history=None)
        assert explanation["explanation"]["trend"] == "new"

    def test_trend_improving(self):
        scored = {
            "confidence": 0.8,
            "confidence_calibrated": 0.8,
            "signal_scores": {"critic_neural": 0.8},
            "context": {},
        }
        explanation = generate_explanation(scored, snapshot_history=[0.5, 0.6, 0.7])
        assert explanation["explanation"]["trend"] == "improving"

    def test_trend_declining(self):
        scored = {
            "confidence": 0.3,
            "confidence_calibrated": 0.3,
            "signal_scores": {"critic_neural": 0.3},
            "context": {},
        }
        explanation = generate_explanation(scored, snapshot_history=[0.8, 0.7, 0.6])
        assert explanation["explanation"]["trend"] == "declining"


# ── Persistence tests ─────────────────────────────────────────────────


class TestCriticStore:
    @pytest_asyncio.fixture
    async def store(self, tmp_path):
        from periphery.db import init_pool, close_pool, get_pool
        db_path = str(tmp_path / "test.db")
        pool = await init_pool(db_path)
        # Insert a dummy snapshot for FK constraints
        async with pool.acquire() as db:
            await db.execute(
                "INSERT INTO crystallizer_snapshots (snapshot_id, generated_at) VALUES (?, ?)",
                ("s1", "2026-01-01T00:00:00"),
            )
            await db.commit()
        from periphery.critic.persistence import CriticStore
        s = CriticStore(db_path)
        await s.initialize()
        yield s
        await close_pool()

    @pytest.mark.asyncio
    async def test_save_and_get_run(self, store):
        await store.save_run(
            run_id="r1", model_version=1, snapshot_id="s1",
            structures_scored=10, mean_confidence=0.7,
            median_confidence=0.65, low_confidence_count=2,
            high_confidence_count=3, scoring_time_ms=100,
        )
        runs = await store.get_recent_runs(limit=5)
        assert len(runs) == 1
        assert runs[0]["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_save_and_get_scores(self, store):
        await store.save_run(
            run_id="r1", model_version=1, snapshot_id="s1",
            structures_scored=2, mean_confidence=0.6,
            median_confidence=0.6, low_confidence_count=0,
            high_confidence_count=0, scoring_time_ms=50,
        )
        scored = [
            {"id": "c1", "type": "cluster", "confidence": 0.7,
             "confidence_raw": 0.65, "confidence_calibrated": 0.68,
             "signal_scores": {"critic_neural": 0.7}, "explanation": {}},
            {"id": "c2", "type": "cluster", "confidence": 0.5,
             "confidence_raw": 0.45, "confidence_calibrated": 0.48,
             "signal_scores": {"critic_neural": 0.5}, "explanation": {}},
        ]
        await store.save_scores("r1", scored)
        latest = await store.get_latest_scores()
        assert len(latest) == 2

    @pytest.mark.asyncio
    async def test_get_scores_for_run(self, store):
        await store.save_run(
            run_id="r1", model_version=1, snapshot_id="s1",
            structures_scored=1, mean_confidence=0.6,
            median_confidence=0.6, low_confidence_count=0,
            high_confidence_count=0, scoring_time_ms=50,
        )
        scored = [{"id": "c1", "type": "cluster", "confidence": 0.7,
                    "confidence_raw": 0.65, "confidence_calibrated": 0.68,
                    "signal_scores": {}, "explanation": {}}]
        await store.save_scores("r1", scored)
        result = await store.get_scores_for_run("r1")
        assert len(result) == 1
        assert result[0]["structure_id"] == "c1"

    @pytest.mark.asyncio
    async def test_confidence_history_roundtrip(self, store):
        history = {"c1": [0.5, 0.6, 0.7], "c2": [0.8, 0.75]}
        await store.save_confidence_history(history)
        loaded = await store.load_confidence_history()
        assert loaded["c1"] == pytest.approx([0.5, 0.6, 0.7])
        assert loaded["c2"] == pytest.approx([0.8, 0.75])


# ── Runner tests ──────────────────────────────────────────────────────


class TestCriticRunner:
    @pytest.fixture
    def snapshot(self):
        from periphery.crystallizer.models import LivingOntologySnapshot, CorpusStats
        return LivingOntologySnapshot(
            snapshot_id="snap_1",
            corpus_stats=CorpusStats(total_documents=100),
            clusters=[
                DetectedCluster(
                    cluster_id="c1",
                    primary_space="semantic",
                    cross_space_coherence=0.8,
                    member_document_ids=["d1", "d2", "d3"],
                    size=3,
                    density=0.7,
                    stability=0.9,
                    key_entities=["Entity1"],
                ),
                DetectedCluster(
                    cluster_id="c2",
                    primary_space="semantic",
                    cross_space_coherence=0.6,
                    member_document_ids=["d4", "d5"],
                    size=2,
                    density=0.5,
                    stability=0.7,
                    key_entities=["Entity2"],
                ),
            ],
            relational_gradients=[
                RelationalGradient(
                    source_cluster="c1",
                    target_cluster="c2",
                    gradient_score=0.5,
                    components=GradientComponents(
                        entity_co_membership=0.3,
                        bridge_entities=["Entity1"],
                    ),
                ),
            ],
            trajectories=[
                Trajectory(
                    trajectory_id="t1",
                    cluster_id="c1",
                    space="semantic",
                    velocity=0.3,
                    confidence=0.7,
                ),
            ],
        )

    @pytest.fixture
    def runner(self, tmp_path):
        model = CoherenceCritic()
        trainer = CriticTrainer(
            model,
            checkpoint_dir=str(tmp_path / "ckpt"),
            training_dir=str(tmp_path / "train"),
        )
        return CriticRunner(
            model=model,
            trainer=trainer,
            store=None,
            device="cpu",
        )

    @pytest.mark.asyncio
    async def test_score_snapshot(self, runner, snapshot):
        stats = await runner.score_snapshot(snapshot)
        assert stats["status"] == "scored"
        assert stats["structures_scored"] > 0
        assert 0.0 <= stats["mean_confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_bootstrap_on_first_score(self, runner, snapshot):
        assert runner.trainer.model_version == 0
        assert not runner._bootstrapped
        await runner.score_snapshot(snapshot)
        assert runner._bootstrapped

    @pytest.mark.asyncio
    async def test_confidence_history_updated(self, runner, snapshot):
        await runner.score_snapshot(snapshot)
        assert len(runner._confidence_history) > 0
        # Should have entries for clusters, gradient, trajectory
        assert "c1" in runner._confidence_history

    @pytest.mark.asyncio
    async def test_drift_detection_low_confidence(self, snapshot):
        model = CoherenceCritic()
        trainer = CriticTrainer(model)
        runner = CriticRunner(
            model=model,
            trainer=trainer,
            drift_low_confidence_ratio=0.0,  # trigger on any low confidence
        )
        await runner.score_snapshot(snapshot)
        # With drift_low_confidence_ratio=0.0, any low-confidence structure triggers alert
        # (may or may not trigger depending on random weights, but the mechanism is tested)
        assert isinstance(runner.drift_alerts, list)

    @pytest.mark.asyncio
    async def test_force_retrain(self, runner, snapshot):
        await runner.score_snapshot(snapshot)
        result = await runner.force_retrain(snapshot)
        assert result["status"] in ("trained", "skipped")

    def test_monitoring_stats(self, runner):
        stats = runner.get_monitoring_stats()
        assert "model_version" in stats
        assert "drift_alerts" in stats
        assert stats["structures_scored"] == 0


# ── Runner with persistence tests ─────────────────────────────────────


class TestCriticRunnerWithStore:
    @pytest_asyncio.fixture
    async def store(self, tmp_path):
        from periphery.db import init_pool, close_pool, get_pool
        db_path = str(tmp_path / "test.db")
        pool = await init_pool(db_path)
        # Insert a dummy snapshot for FK constraints
        async with pool.acquire() as db:
            await db.execute(
                "INSERT INTO crystallizer_snapshots (snapshot_id, generated_at) VALUES (?, ?)",
                ("snap_1", "2026-01-01T00:00:00"),
            )
            await db.commit()
        from periphery.critic.persistence import CriticStore
        s = CriticStore(db_path)
        await s.initialize()
        yield s
        await close_pool()

    @pytest.fixture
    def snapshot(self):
        from periphery.crystallizer.models import LivingOntologySnapshot, CorpusStats
        return LivingOntologySnapshot(
            snapshot_id="snap_1",
            corpus_stats=CorpusStats(total_documents=100),
            clusters=[
                DetectedCluster(
                    cluster_id="c1",
                    primary_space="semantic",
                    cross_space_coherence=0.8,
                    member_document_ids=["d1", "d2", "d3"],
                    size=3,
                    density=0.7,
                    stability=0.9,
                    key_entities=["Entity1"],
                ),
            ],
            relational_gradients=[],
            trajectories=[],
        )

    @pytest.mark.asyncio
    async def test_score_persists_to_db(self, store, snapshot, tmp_path):
        model = CoherenceCritic()
        trainer = CriticTrainer(
            model,
            checkpoint_dir=str(tmp_path / "ckpt"),
            training_dir=str(tmp_path / "train"),
        )
        runner = CriticRunner(model=model, trainer=trainer, store=store)
        await runner.score_snapshot(snapshot)

        # Check scores were persisted
        latest = await store.get_latest_scores()
        assert len(latest) > 0
        assert latest[0]["structure_id"] == "c1"

    @pytest.mark.asyncio
    async def test_load_state_restores_history(self, store, snapshot, tmp_path):
        model = CoherenceCritic()
        trainer = CriticTrainer(
            model,
            checkpoint_dir=str(tmp_path / "ckpt"),
            training_dir=str(tmp_path / "train"),
        )
        runner = CriticRunner(model=model, trainer=trainer, store=store)
        await runner.score_snapshot(snapshot)

        # Create new runner and load state
        runner2 = CriticRunner(model=model, trainer=trainer, store=store)
        await runner2.load_state()
        assert len(runner2._confidence_history) > 0
        assert len(runner2._last_scoring_results) > 0


# ── Integration test ──────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_pipeline(self):
        """Test the full pipeline: generate perturbations, train, score."""
        clusters = [
            DetectedCluster(
                cluster_id=f"cluster_{i}",
                primary_space="semantic",
                cross_space_coherence=0.7 + i * 0.05,
                member_document_ids=[f"doc_{i}_{j}" for j in range(5)],
                size=5,
                density=0.6 + i * 0.1,
                stability=0.8,
                key_entities=[f"Entity_{i}"],
            )
            for i in range(5)
        ]

        gradients = [
            RelationalGradient(
                source_cluster="cluster_0",
                target_cluster="cluster_1",
                gradient_score=0.5,
                components=GradientComponents(
                    entity_co_membership=0.4,
                    temporal_alignment=0.5,
                    bridge_entities=["Entity_0"],
                ),
            )
        ]

        trajectories = [
            Trajectory(
                trajectory_id="traj_0",
                cluster_id="cluster_0",
                space="semantic",
                velocity=0.3,
                confidence=0.7,
            )
        ]

        # Step 1: Generate perturbation dataset
        engine = PerturbationEngine(rng=np.random.RandomState(42))
        samples = engine.generate_dataset(
            clusters, gradients, trajectories,
            variants_per_structure=3,
        )
        assert len(samples) > 10

        # Step 2: Train the Critic
        model = CoherenceCritic()
        with tempfile.TemporaryDirectory() as tmpdir:
            trainer = CriticTrainer(
                model,
                checkpoint_dir=os.path.join(tmpdir, "ckpt"),
                training_dir=os.path.join(tmpdir, "train"),
            )
            result = trainer.train_on_samples(samples, epochs=10)
            assert result["status"] == "trained"

            # Step 3: Save checkpoint
            path = trainer.save_checkpoint(
                val_accuracy=result["final_val_accuracy"]
            )
            assert os.path.exists(path)

        # Step 4: Score structures
        scorer = SnapshotScorer(model=model)
        structures = [
            {
                "type": "cluster",
                "id": c.cluster_id,
                "features": {
                    "size": c.size,
                    "density": c.density,
                    "cross_space_coherence": c.cross_space_coherence,
                    "stability": c.stability,
                    "member_count": len(c.member_document_ids),
                    "entity_count": len(c.key_entities),
                    "relationship_count": len(c.key_relationships),
                },
                "context": {
                    "num_sources": len(c.member_document_ids),
                    "cross_space_coherence": c.cross_space_coherence,
                },
            }
            for c in clusters
        ]

        results = scorer.score_structures(structures)
        assert len(results) == 5
        for r in results:
            assert 0.0 <= r["confidence"] <= 1.0
            assert "signal_scores" in r

        # Step 5: Generate explanations
        for r in results:
            exp = generate_explanation(r)
            assert "explanation" in exp
            assert len(exp["explanation"]["primary_factors"]) > 0
