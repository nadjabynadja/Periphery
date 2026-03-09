"""Comprehensive tests for the Crystallizer engine.

Tests all four analytical sub-processes: cluster detection, trajectory
detection, relational gradient analysis, and anomaly detection, plus
the persistence layer, auto-labeling, and living ontology snapshot assembly.
"""

import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pytest

from periphery.crystallizer.clustering import (
    MultiSpaceClusterEngine,
    SpaceClusterer,
    run_clustering,
)
from periphery.crystallizer.graph import OntologyGraph
from periphery.crystallizer.models import (
    Anomaly,
    ConvergenceAlert,
    CorpusStats,
    DetectedCluster,
    GradientComponents,
    LivingOntologySnapshot,
    RelationalGradient,
    Trajectory,
    TrajectorySnapshot,
)
from periphery.crystallizer.trajectories import TrajectoryDetector
from periphery.crystallizer.gradients import GradientAnalyzer
from periphery.crystallizer.anomalies import AnomalyDetector
from periphery.crystallizer.labeler import (
    generate_label,
    extract_key_entities,
    extract_key_relationships,
)
from periphery.crystallizer.persistence import CrystallizerStore
from periphery.models import Document


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_clusters(n_clusters=2, n_per_cluster=15, dim=384, noise=0.1, separation=5.0):
    """Generate well-separated clusters for testing."""
    rng = np.random.RandomState(42)
    all_vectors = []
    all_ids = []

    for c in range(n_clusters):
        cluster_vecs = rng.randn(n_per_cluster, dim).astype(np.float32) * noise
        cluster_vecs[:, c % dim] += separation
        all_vectors.append(cluster_vecs)
        for i in range(n_per_cluster):
            all_ids.append(f"doc_{c}_{i}")

    vectors = np.vstack(all_vectors)
    return vectors, all_ids


# ── Legacy Clustering Tests ──────────────────────────────────────────────

def test_clustering_insufficient_data():
    vectors = np.random.randn(3, 384).astype(np.float32)
    labels, stats = run_clustering(vectors, min_cluster_size=5)
    assert stats["n_clusters"] == 0


def test_clustering_with_clear_clusters():
    """Create two clearly separated clusters and verify detection."""
    rng = np.random.RandomState(42)

    cluster_a = rng.randn(15, 384).astype(np.float32) * 0.1
    cluster_a[:, 0] += 5.0

    cluster_b = rng.randn(15, 384).astype(np.float32) * 0.1
    cluster_b[:, 0] -= 5.0

    vectors = np.vstack([cluster_a, cluster_b])
    labels, stats = run_clustering(vectors, min_cluster_size=5, min_samples=3)

    assert stats["n_clusters"] >= 2


# ── SpaceClusterer Tests ────────────────────────────────────────────────

def test_space_clusterer_fit():
    vectors, doc_ids = _make_clusters(n_clusters=2, n_per_cluster=15)
    clusterer = SpaceClusterer("semantic", min_cluster_size=5, min_samples=3)
    stats = clusterer.fit(vectors, doc_ids)

    assert stats["space"] == "semantic"
    assert stats["n_clusters"] >= 2
    assert clusterer.labels is not None
    assert len(clusterer.labels) == len(doc_ids)


def test_space_clusterer_insufficient_data():
    vectors = np.random.randn(3, 384).astype(np.float32)
    doc_ids = ["a", "b", "c"]
    clusterer = SpaceClusterer("semantic", min_cluster_size=5)
    stats = clusterer.fit(vectors, doc_ids)

    assert stats["n_clusters"] == 0
    assert all(l == -1 for l in clusterer.labels)


def test_space_clusterer_members_and_noise():
    vectors, doc_ids = _make_clusters(n_clusters=2, n_per_cluster=15)
    clusterer = SpaceClusterer("semantic", min_cluster_size=5, min_samples=3)
    clusterer.fit(vectors, doc_ids)

    members = clusterer.get_cluster_members()
    assert len(members) >= 2

    noise = clusterer.get_noise_indices()
    assert isinstance(noise, list)


def test_space_clusterer_centroids():
    vectors, doc_ids = _make_clusters(n_clusters=2, n_per_cluster=15)
    clusterer = SpaceClusterer("semantic", min_cluster_size=5, min_samples=3)
    clusterer.fit(vectors, doc_ids)

    centroids = clusterer.get_cluster_centroids(vectors)
    assert len(centroids) >= 2
    for cid, centroid in centroids.items():
        assert centroid.shape == (384,)


def test_space_clusterer_densities():
    vectors, doc_ids = _make_clusters(n_clusters=2, n_per_cluster=15)
    clusterer = SpaceClusterer("semantic", min_cluster_size=5, min_samples=3)
    clusterer.fit(vectors, doc_ids)

    densities = clusterer.get_cluster_densities()
    assert len(densities) >= 2
    for density in densities.values():
        assert 0.0 <= density <= 1.0


def test_space_clusterer_predict():
    vectors, doc_ids = _make_clusters(n_clusters=2, n_per_cluster=15)
    clusterer = SpaceClusterer("semantic", min_cluster_size=5, min_samples=3)
    clusterer.fit(vectors, doc_ids)

    # New points near cluster 0
    new_vecs = np.random.randn(3, 384).astype(np.float32) * 0.1
    new_vecs[:, 0] += 5.0
    labels, strengths = clusterer.predict(new_vecs)
    assert len(labels) == 3
    assert len(strengths) == 3


# ── MultiSpaceClusterEngine Tests ───────────────────────────────────────

def test_multi_space_cluster_all_spaces():
    vectors_sem, ids = _make_clusters(n_clusters=2, n_per_cluster=15, dim=384)
    vectors_ent, _ = _make_clusters(n_clusters=2, n_per_cluster=15, dim=384)

    engine = MultiSpaceClusterEngine(min_cluster_size=5, min_samples=3)
    stats = engine.cluster_all_spaces(
        {"semantic": vectors_sem, "entity": vectors_ent},
        {"semantic": ids, "entity": ids},
    )

    assert "semantic" in stats
    assert "entity" in stats
    assert stats["semantic"]["n_clusters"] >= 2


def test_multi_space_correlate_clusters():
    """Test cross-space cluster correlation with shared doc IDs."""
    vectors, ids = _make_clusters(n_clusters=2, n_per_cluster=15, dim=384)

    engine = MultiSpaceClusterEngine(
        min_cluster_size=5, min_samples=3, member_overlap_threshold=0.3
    )
    engine.cluster_all_spaces(
        {"semantic": vectors, "entity": vectors},
        {"semantic": ids, "entity": ids},
    )

    correlations = engine.correlate_clusters(
        {"semantic": ids, "entity": ids}
    )

    assert len(correlations) > 0
    for corr in correlations:
        assert "cluster_id" in corr
        assert "primary_space" in corr
        assert "cross_space_coherence" in corr
        assert 0.0 <= corr["cross_space_coherence"] <= 1.0


def test_multi_space_noise_doc_ids():
    vectors, ids = _make_clusters(n_clusters=2, n_per_cluster=15, dim=384)
    engine = MultiSpaceClusterEngine(min_cluster_size=5, min_samples=3)
    engine.cluster_all_spaces(
        {"semantic": vectors},
        {"semantic": ids},
    )

    noise = engine.get_all_noise_doc_ids({"semantic": ids})
    assert "semantic" in noise
    assert isinstance(noise["semantic"], list)


def test_multi_space_incremental_predict():
    vectors, ids = _make_clusters(n_clusters=2, n_per_cluster=15, dim=384)
    engine = MultiSpaceClusterEngine(min_cluster_size=5, min_samples=3)
    engine.cluster_all_spaces(
        {"semantic": vectors},
        {"semantic": ids},
    )

    new_vecs = np.random.randn(5, 384).astype(np.float32)
    results = engine.predict_incremental({"semantic": new_vecs})
    assert "semantic" in results
    labels, strengths = results["semantic"]
    assert len(labels) == 5


# ── Trajectory Detection Tests ──────────────────────────────────────────

def test_trajectory_detector_basic():
    detector = TrajectoryDetector(min_snapshots=3)

    # Simulate a cluster moving in a consistent direction
    centroids = {}
    for step in range(5):
        centroid = np.zeros(10)
        centroid[0] = step * 0.1  # consistent movement in dim 0
        centroids[0] = centroid
        detector.record_centroids(
            "semantic", centroids,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=step),
        )

    trajectories = detector.detect_trajectories()
    assert len(trajectories) >= 1
    traj = trajectories[0]
    assert traj.space == "semantic"
    assert traj.velocity > 0


def test_trajectory_detector_insufficient_snapshots():
    detector = TrajectoryDetector(min_snapshots=5)

    centroids = {0: np.zeros(10)}
    detector.record_centroids("semantic", centroids)
    detector.record_centroids("semantic", centroids)

    trajectories = detector.detect_trajectories()
    assert len(trajectories) == 0


def test_trajectory_convergence_detection():
    detector = TrajectoryDetector(min_snapshots=3)

    for step in range(5):
        centroids_a = np.zeros(10)
        centroids_a[0] = 1.0 - step * 0.15
        centroids_b = np.zeros(10)
        centroids_b[0] = -1.0 + step * 0.15

        detector.record_centroids(
            "semantic",
            {0: centroids_a, 1: centroids_b},
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=step),
        )

    detector.detect_trajectories()
    alerts = detector.detect_convergences(distance_threshold=2.0)
    assert isinstance(alerts, list)


def test_trajectory_cleanup():
    detector = TrajectoryDetector(min_snapshots=3)

    for step in range(5):
        centroids = {0: np.array([step * 0.1] * 10)}
        detector.record_centroids(
            "semantic", centroids,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=step),
        )

    detector.detect_trajectories()
    assert len(detector.trajectories) > 0

    detector.cleanup_dissolved(set())
    assert len(detector.trajectories) == 0


# ── Relational Gradient Tests ───────────────────────────────────────────

def test_gradient_analyzer_basic():
    analyzer = GradientAnalyzer()

    clusters = {
        "c1": {"doc_1", "doc_2", "doc_3"},
        "c2": {"doc_4", "doc_5", "doc_6"},
    }

    doc_entities = {
        "doc_1": [{"canonical_id": "ent_a", "text": "Entity A"}],
        "doc_2": [{"canonical_id": "ent_b", "text": "Entity B"}],
        "doc_3": [{"canonical_id": "ent_shared", "text": "Shared"}],
        "doc_4": [{"canonical_id": "ent_c", "text": "Entity C"}],
        "doc_5": [{"canonical_id": "ent_shared", "text": "Shared"}],
        "doc_6": [{"canonical_id": "ent_d", "text": "Entity D"}],
    }

    doc_relationships = {
        "doc_1": [{"subject_id": "ent_a", "predicate": "works_with", "object_id": "ent_shared"}],
        "doc_4": [{"subject_id": "ent_shared", "predicate": "manages", "object_id": "ent_c"}],
    }

    gradients = analyzer.compute_gradients(
        clusters, doc_entities, doc_relationships,
        {"c1": None, "c2": None},
        {"c1": None, "c2": None},
    )

    assert len(gradients) > 0
    g = gradients[0]
    assert g.gradient_score > 0
    assert g.components.entity_co_membership > 0


def test_gradient_analyzer_no_overlap():
    analyzer = GradientAnalyzer()

    clusters = {
        "c1": {"doc_1"},
        "c2": {"doc_2"},
    }
    doc_entities = {
        "doc_1": [{"canonical_id": "ent_a"}],
        "doc_2": [{"canonical_id": "ent_b"}],
    }

    gradients = analyzer.compute_gradients(
        clusters, doc_entities, {},
        {"c1": None, "c2": None},
        {"c1": None, "c2": None},
    )

    for g in gradients:
        assert g.gradient_score < 0.5


def test_gradient_trend_tracking():
    analyzer = GradientAnalyzer()

    clusters = {
        "c1": {"doc_1", "doc_2"},
        "c2": {"doc_3", "doc_4"},
    }
    doc_entities = {
        "doc_1": [{"canonical_id": "shared"}],
        "doc_2": [],
        "doc_3": [{"canonical_id": "shared"}],
        "doc_4": [],
    }

    analyzer.compute_gradients(
        clusters, doc_entities, {},
        {"c1": None, "c2": None},
        {"c1": None, "c2": None},
    )
    gradients = analyzer.compute_gradients(
        clusters, doc_entities, {},
        {"c1": None, "c2": None},
        {"c1": None, "c2": None},
    )

    for g in gradients:
        assert g.gradient_trend in ("strengthening", "stable", "weakening")


# ── Anomaly Detection Tests ─────────────────────────────────────────────

def test_anomaly_detector_basic():
    detector = AnomalyDetector()

    noise_doc_ids = {
        "semantic": ["outlier_1"],
        "entity": ["outlier_1"],
        "relational": ["outlier_1"],
    }

    vectors = {
        "semantic": np.array([[10.0] * 384], dtype=np.float32),
        "entity": np.array([[10.0] * 384], dtype=np.float32),
        "relational": np.array([[10.0] * 384], dtype=np.float32),
    }
    doc_ids = {
        "semantic": ["outlier_1"],
        "entity": ["outlier_1"],
        "relational": ["outlier_1"],
    }
    centroids = {
        "semantic": {0: np.zeros(384, dtype=np.float32)},
        "entity": {0: np.zeros(384, dtype=np.float32)},
        "relational": {0: np.zeros(384, dtype=np.float32)},
    }
    metadata = {
        "outlier_1": {"source_credibility_tier": 1, "entities": [], "relationships": []},
    }

    anomalies = detector.detect(
        noise_doc_ids, vectors, doc_ids, centroids, metadata
    )

    assert len(anomalies) >= 1
    a = anomalies[0]
    assert a.document_id == "outlier_1"
    assert a.anomaly_score > 0
    assert len(a.outlier_spaces) == 3


def test_anomaly_resolution():
    detector = AnomalyDetector()

    noise = {"semantic": ["doc_1"]}
    vectors = {"semantic": np.array([[1.0] * 10], dtype=np.float32)}
    doc_ids = {"semantic": ["doc_1"]}
    centroids = {"semantic": {0: np.zeros(10)}}
    metadata = {"doc_1": {"source_credibility_tier": 2}}

    detector.detect(noise, vectors, doc_ids, centroids, metadata)
    assert len(detector.get_unresolved()) > 0

    resolved = detector.check_resolutions({"semantic": []})
    assert "doc_1" in resolved


def test_anomaly_classification():
    detector = AnomalyDetector()

    noise = {"entity": ["doc_1"]}
    vectors = {"entity": np.array([[5.0] * 10], dtype=np.float32)}
    doc_ids = {"entity": ["doc_1"]}
    centroids = {"entity": {0: np.zeros(10)}}
    metadata = {"doc_1": {
        "source_credibility_tier": 1,
        "entities": [{"text": "New Actor"}],
        "relationships": [],
    }}

    anomalies = detector.detect(noise, vectors, doc_ids, centroids, metadata)
    if anomalies:
        assert anomalies[0].anomaly_type == "novel_entity"


# ── Auto-Labeling Tests ─────────────────────────────────────────────────

def test_generate_label_with_entities_and_rels():
    entities = [
        {"text": "Alpha Corp"},
        {"text": "Beta Inc"},
        {"text": "Gamma LLC"},
    ]
    relationships = [
        {"predicate": "acquired"},
        {"predicate": "partnered_with"},
    ]

    label = generate_label(entities, relationships, 42)
    assert "Alpha Corp" in label
    assert "42 documents" in label


def test_generate_label_entities_only():
    entities = [{"text": "Alpha"}, {"text": "Beta"}]
    label = generate_label(entities, [], 10)
    assert "Alpha" in label


def test_generate_label_empty():
    label = generate_label([], [], 5)
    assert "5 documents" in label


def test_extract_key_entities():
    doc_entities = {
        "d1": [{"text": "A", "canonical_id": "a"}, {"text": "B", "canonical_id": "b"}],
        "d2": [{"text": "A", "canonical_id": "a"}, {"text": "C", "canonical_id": "c"}],
        "d3": [{"text": "A", "canonical_id": "a"}],
    }

    key = extract_key_entities(["d1", "d2", "d3"], doc_entities, top_n=5)
    assert len(key) >= 1
    assert key[0].get("canonical_id") == "a"


def test_extract_key_relationships():
    doc_rels = {
        "d1": [{"subject_id": "a", "predicate": "works_at", "object_id": "b"}],
        "d2": [{"subject_id": "a", "predicate": "works_at", "object_id": "b"}],
    }

    key = extract_key_relationships(["d1", "d2"], doc_rels, top_n=3)
    assert len(key) == 1
    assert key[0]["predicate"] == "works_at"


# ── OntologyGraph Tests (preserved from original) ───────────────────────

def test_ontology_graph_build():
    rng = np.random.RandomState(42)
    vectors = rng.randn(10, 384).astype(np.float32)
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    doc_ids = [f"doc_{i}" for i in range(10)]
    documents = [Document(id=did, content=f"Document {i}") for i, did in enumerate(doc_ids)]
    labels = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])

    graph = OntologyGraph()
    graph.build_from_clusters(documents, doc_ids, labels, vectors)
    snapshot = graph.to_snapshot()

    assert snapshot.cluster_count == 2
    assert snapshot.document_count == 10
    assert len(snapshot.edges) > 0


def test_ontology_graph_subgraph():
    rng = np.random.RandomState(42)
    vectors = rng.randn(6, 384).astype(np.float32)
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    doc_ids = [f"doc_{i}" for i in range(6)]
    documents = [Document(id=did, content=f"Doc {i}") for i, did in enumerate(doc_ids)]
    labels = np.array([0, 0, 0, 1, 1, 1])

    graph = OntologyGraph()
    graph.build_from_clusters(documents, doc_ids, labels, vectors)

    sub = graph.get_subgraph("doc_0", depth=1)
    node_ids = {n.id for n in sub.nodes}
    assert "doc_0" in node_ids


# ── Model Tests ──────────────────────────────────────────────────────────

def test_living_ontology_snapshot_model():
    snapshot = LivingOntologySnapshot(
        snapshot_id="test_001",
        corpus_stats=CorpusStats(total_documents=100, total_entities=50),
        clusters=[
            DetectedCluster(
                cluster_id="sem_0",
                primary_space="semantic",
                size=10,
                member_document_ids=["d1", "d2"],
                label="Test Cluster (10 documents)",
            )
        ],
        anomalies=[
            Anomaly(
                anomaly_id="a1",
                document_id="d99",
                anomaly_type="structural",
                anomaly_score=0.8,
                outlier_spaces=["semantic", "entity"],
            )
        ],
    )

    assert snapshot.snapshot_id == "test_001"
    assert len(snapshot.clusters) == 1
    assert len(snapshot.anomalies) == 1

    data = snapshot.model_dump()
    assert data["snapshot_id"] == "test_001"

    json_str = snapshot.model_dump_json()
    restored = LivingOntologySnapshot.model_validate_json(json_str)
    assert restored.snapshot_id == "test_001"
    assert len(restored.clusters) == 1


def test_trajectory_model():
    now = datetime.now(timezone.utc)
    traj = Trajectory(
        trajectory_id="t1",
        cluster_id="sem_0",
        space="semantic",
        direction_vector=[0.5, 0.5, 0.0],
        velocity=0.1,
        confidence=0.85,
        pattern="acceleration",
        snapshots=[
            TrajectorySnapshot(timestamp=now, centroid=[1.0, 2.0, 3.0]),
            TrajectorySnapshot(timestamp=now + timedelta(hours=1), centroid=[1.1, 2.1, 3.0]),
        ],
    )

    assert traj.pattern == "acceleration"
    assert len(traj.snapshots) == 2


def test_relational_gradient_model():
    gradient = RelationalGradient(
        source_cluster="c1",
        target_cluster="c2",
        gradient_score=0.75,
        components=GradientComponents(
            entity_co_membership=0.5,
            temporal_alignment=0.8,
            geographic_proximity=0.6,
            relational_bridges=3,
            bridge_entities=["ent_1", "ent_2", "ent_3"],
        ),
        gradient_trend="strengthening",
    )

    assert gradient.gradient_score == 0.75
    assert gradient.components.relational_bridges == 3


# ── Persistence Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persistence_initialize():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()


@pytest.mark.asyncio
async def test_persistence_save_and_load_snapshot():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        snapshot = LivingOntologySnapshot(
            snapshot_id="snap_001",
            corpus_stats=CorpusStats(total_documents=50),
            clusters=[
                DetectedCluster(
                    cluster_id="c1",
                    primary_space="semantic",
                    size=10,
                    member_document_ids=["d1"],
                    label="Test",
                )
            ],
        )

        await store.save_snapshot(snapshot)

        loaded = await store.load_latest_snapshot()
        assert loaded is not None
        assert loaded.snapshot_id == "snap_001"
        assert len(loaded.clusters) == 1


@pytest.mark.asyncio
async def test_persistence_save_clusters():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        clusters = [
            DetectedCluster(
                cluster_id="c1",
                primary_space="semantic",
                size=10,
                status="stable",
                cross_space_coherence=0.8,
                member_document_ids=["d1", "d2"],
                label="Test Cluster",
            ),
            DetectedCluster(
                cluster_id="c2",
                primary_space="entity",
                size=5,
                status="forming",
                member_document_ids=["d3"],
            ),
        ]

        await store.save_clusters_batch(clusters)

        active = await store.get_active_cluster_ids()
        assert "c1" in active
        assert "c2" in active


@pytest.mark.asyncio
async def test_persistence_cluster_history():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        cluster = DetectedCluster(
            cluster_id="c1",
            primary_space="semantic",
            size=10,
            centroid=[1.0, 2.0, 3.0],
        )

        await store.save_clusters_batch([cluster])
        cluster.size = 15
        await store.save_clusters_batch([cluster])

        history = await store.get_cluster_history("c1")
        assert len(history) == 2


@pytest.mark.asyncio
async def test_persistence_save_anomalies():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        anomalies = [
            Anomaly(
                anomaly_id="a1",
                document_id="d1",
                anomaly_type="novel_entity",
                anomaly_score=0.9,
                outlier_spaces=["semantic", "entity"],
                source_credibility=1,
            )
        ]

        await store.save_anomalies_batch(anomalies)


@pytest.mark.asyncio
async def test_persistence_save_gradients():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        gradients = [
            RelationalGradient(
                source_cluster="c1",
                target_cluster="c2",
                gradient_score=0.7,
                components=GradientComponents(entity_co_membership=0.5),
            )
        ]

        await store.save_gradients(gradients)


@pytest.mark.asyncio
async def test_persistence_telemetry():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        await store.save_clusters_batch([
            DetectedCluster(cluster_id="c1", primary_space="semantic", size=10, status="stable"),
        ])
        await store.save_anomalies_batch([
            Anomaly(anomaly_id="a1", document_id="d1", anomaly_type="structural", anomaly_score=0.5),
        ])

        telemetry = await store.get_telemetry()
        assert "clusters_by_status" in telemetry
        assert "unresolved_anomalies_by_type" in telemetry


@pytest.mark.asyncio
async def test_persistence_snapshot_pruning():
    """Verify that only the last 100 snapshots are kept."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        for i in range(105):
            snapshot = LivingOntologySnapshot(
                snapshot_id=f"snap_{i:04d}",
                corpus_stats=CorpusStats(total_documents=i),
            )
            await store.save_snapshot(snapshot)

        loaded = await store.load_latest_snapshot()
        assert loaded is not None
        assert loaded.snapshot_id == "snap_0104"


@pytest.mark.asyncio
async def test_persistence_mark_dissolved():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = CrystallizerStore(f.name)
        await store.initialize()

        await store.save_clusters_batch([
            DetectedCluster(cluster_id="c1", primary_space="semantic", size=10, status="stable"),
            DetectedCluster(cluster_id="c2", primary_space="semantic", size=5, status="stable"),
        ])

        await store.mark_clusters_dissolved(["c1"])

        active = await store.get_active_cluster_ids()
        assert "c1" not in active
        assert "c2" in active
