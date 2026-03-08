import numpy as np

from periphery.crystallizer.clustering import run_clustering
from periphery.crystallizer.graph import OntologyGraph
from periphery.models import Document


def test_clustering_insufficient_data():
    vectors = np.random.randn(3, 384).astype(np.float32)
    labels, stats = run_clustering(vectors, min_cluster_size=5)
    assert stats["n_clusters"] == 0


def test_clustering_with_clear_clusters():
    """Create two clearly separated clusters and verify detection."""
    rng = np.random.RandomState(42)

    # Cluster A: centered at [1, 0, 0, ...]
    cluster_a = rng.randn(15, 384).astype(np.float32) * 0.1
    cluster_a[:, 0] += 5.0

    # Cluster B: centered at [-1, 0, 0, ...]
    cluster_b = rng.randn(15, 384).astype(np.float32) * 0.1
    cluster_b[:, 0] -= 5.0

    vectors = np.vstack([cluster_a, cluster_b])
    labels, stats = run_clustering(vectors, min_cluster_size=5, min_samples=3)

    assert stats["n_clusters"] >= 2


def test_ontology_graph_build():
    rng = np.random.RandomState(42)
    vectors = rng.randn(10, 384).astype(np.float32)
    # Normalize
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
    # Should include at least doc_0 and its neighbors
    node_ids = {n.id for n in sub.nodes}
    assert "doc_0" in node_ids
