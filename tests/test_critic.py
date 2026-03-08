import numpy as np
import torch

from periphery.critic.network import CoherenceNet, StructuralDiscriminator
from periphery.critic.scoring import score_cluster, score_all_clusters
from periphery.critic.trainer import AdversarialTrainer


def test_coherence_net_forward():
    model = CoherenceNet(dim=384)
    # Input: concatenated pair of embeddings
    pairs = torch.randn(8, 768)
    scores = model(pairs)
    assert scores.shape == (8,)
    assert (scores >= 0).all() and (scores <= 1).all()


def test_discriminator_forward():
    model = StructuralDiscriminator(dim=384)
    pairs = torch.randn(8, 768)
    scores = model(pairs)
    assert scores.shape == (8,)
    assert (scores >= 0).all() and (scores <= 1).all()


def test_score_cluster():
    model = CoherenceNet(dim=384)
    vectors = np.random.randn(10, 384).astype(np.float32)
    score = score_cluster(model, vectors)
    assert 0.0 <= score <= 1.0


def test_score_all_clusters():
    model = CoherenceNet(dim=384)
    vectors = np.random.randn(20, 384).astype(np.float32)
    labels = np.array([0] * 10 + [1] * 10)
    scores = score_all_clusters(model, vectors, labels)
    assert 0 in scores
    assert 1 in scores
    assert all(0 <= v <= 1 for v in scores.values())


def test_adversarial_training():
    model = CoherenceNet(dim=384)
    trainer = AdversarialTrainer(model, device="cpu")

    # Create two clear clusters
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


def test_adversarial_training_insufficient_clusters():
    model = CoherenceNet(dim=384)
    trainer = AdversarialTrainer(model, device="cpu")

    vectors = np.random.randn(10, 384).astype(np.float32)
    labels = np.array([0] * 10)  # Only one cluster

    results = trainer.train_multiple(vectors, labels, epochs=1)
    assert results[0]["status"] == "skipped"
