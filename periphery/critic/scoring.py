import numpy as np
import torch

from periphery.critic.network import CoherenceNet


def score_cluster(
    model: CoherenceNet, vectors: np.ndarray, max_pairs: int = 100
) -> float:
    """Score coherence of a cluster by averaging pairwise scores."""
    if vectors.shape[0] < 2:
        return 1.0  # Single-element clusters are trivially coherent

    model.eval()
    n = vectors.shape[0]

    # Sample pairs if cluster is large
    if n * (n - 1) // 2 > max_pairs:
        indices = np.random.choice(n, size=(max_pairs, 2), replace=True)
        # Ensure we don't pair element with itself
        mask = indices[:, 0] != indices[:, 1]
        indices = indices[mask]
    else:
        indices = np.array([(i, j) for i in range(n) for j in range(i + 1, n)])

    if len(indices) == 0:
        return 1.0

    pairs = np.concatenate([vectors[indices[:, 0]], vectors[indices[:, 1]]], axis=1)
    pairs_tensor = torch.tensor(pairs, dtype=torch.float32)

    with torch.no_grad():
        scores = model(pairs_tensor)

    return float(scores.mean().item())


def score_document(
    model: CoherenceNet, doc_vector: np.ndarray, cluster_vectors: np.ndarray
) -> float:
    """Score how well a document fits within its cluster."""
    if cluster_vectors.shape[0] == 0:
        return 0.0

    model.eval()
    n = min(cluster_vectors.shape[0], 50)  # Sample for large clusters
    indices = np.random.choice(cluster_vectors.shape[0], size=n, replace=False)
    sampled = cluster_vectors[indices]

    doc_repeated = np.repeat(doc_vector.reshape(1, -1), n, axis=0)
    pairs = np.concatenate([doc_repeated, sampled], axis=1)
    pairs_tensor = torch.tensor(pairs, dtype=torch.float32)

    with torch.no_grad():
        scores = model(pairs_tensor)

    return float(scores.mean().item())


def score_all_clusters(
    model: CoherenceNet, vectors: np.ndarray, labels: np.ndarray
) -> dict[int, float]:
    """Score coherence for all clusters."""
    scores = {}
    unique_labels = set(labels) - {-1}

    for label in unique_labels:
        mask = labels == label
        cluster_vecs = vectors[mask]
        scores[int(label)] = score_cluster(model, cluster_vecs)

    return scores
