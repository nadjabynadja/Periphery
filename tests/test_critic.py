"""Comprehensive tests for the Continuous Critic (Layer 3).

Tests cover:
  - CoherenceCritic network (forward pass, output bounds)
  - Feature vector extraction (all structure types, padding)
  - Perturbation engine (dataset generation, severity levels)
  - CriticTrainer (training, checkpointing, rollback)
  - Scoring pipeline (calibration, ensemble, propagation)
  - Confidence explanations
  - Legacy compatibility (AdversarialTrainer, pair-based scoring)
"""

import os
import tempfile

import numpy as np
import pytest
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
from periphery.critic.network import CoherenceCritic, CoherenceNet, StructuralDiscriminator
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
    score_all_clusters,
    score_cluster,
)
from periphery.critic.trainer import AdversarialTrainer, CriticTrainer
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

    def test_backward_compat_separate_classes(self):
        # CoherenceNet (legacy, pair-based) and CoherenceCritic (new, feature-based)
        # are separate classes with different input signatures
        assert CoherenceNet is not CoherenceCritic
        assert hasattr(CoherenceNet(dim=384), 'dim')
        assert hasattr(CoherenceCritic(), 'input_dim')


class TestStructuralDiscriminator:
    def test_forward(self):
        model = StructuralDiscriminator(dim=384)
        pairs = torch.randn(8, 768)
        scores = model(pairs)
        assert scores.shape == (8,)
        assert (scores >= 0).all() and (scores <= 1).all()


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


# ── Legacy compatibility tests ─────────────────────────────────────────


class TestLegacyCompatibility:
    def test_coherence_net_forward(self):
        model = CoherenceNet(dim=384)
        pairs = torch.randn(8, 768)
        scores = model(pairs)
        assert scores.shape == (8,)
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_discriminator_forward(self):
        model = StructuralDiscriminator(dim=384)
        pairs = torch.randn(8, 768)
        scores = model(pairs)
        assert scores.shape == (8,)
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_score_cluster(self):
        model = CoherenceNet(dim=384)
        vectors = np.random.randn(10, 384).astype(np.float32)
        score = score_cluster(model, vectors)
        assert 0.0 <= score <= 1.0

    def test_score_all_clusters(self):
        model = CoherenceNet(dim=384)
        vectors = np.random.randn(20, 384).astype(np.float32)
        labels = np.array([0] * 10 + [1] * 10)
        scores = score_all_clusters(model, vectors, labels)
        assert 0 in scores
        assert 1 in scores
        assert all(0 <= v <= 1 for v in scores.values())

    def test_adversarial_training(self):
        model = CoherenceNet(dim=384)
        trainer = AdversarialTrainer(model, device="cpu")

        rng = np.random.RandomState(42)
        cluster_a = rng.randn(15, 384).astype(np.float32) * 0.1
        cluster_a[:, 0] += 3.0
        cluster_b = rng.randn(15, 384).astype(np.float32) * 0.1
        cluster_b[:, 0] -= 3.0

        vectors = np.vstack([cluster_a, cluster_b])
        labels = np.array([0] * 15 + [1] * 15)

        results = trainer.train_multiple(vectors, labels, epochs=3)
        assert len(results) == 3
        assert results[0]["status"] == "trained"

    def test_adversarial_training_insufficient_clusters(self):
        model = CoherenceNet(dim=384)
        trainer = AdversarialTrainer(model, device="cpu")

        vectors = np.random.randn(10, 384).astype(np.float32)
        labels = np.array([0] * 10)

        results = trainer.train_multiple(vectors, labels, epochs=1)
        assert results[0]["status"] == "skipped"


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
