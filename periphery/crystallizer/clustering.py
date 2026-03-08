import numpy as np
import hdbscan


def run_clustering(
    vectors: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: int = 3,
) -> tuple[np.ndarray, dict]:
    """
    Run HDBSCAN density-based clustering over embedding vectors.

    Returns:
        labels: array of cluster labels (-1 = noise)
        stats: clustering statistics
    """
    if vectors.shape[0] < min_cluster_size:
        return np.full(vectors.shape[0], -1), {
            "n_clusters": 0,
            "noise_count": vectors.shape[0],
            "noise_ratio": 1.0,
        }

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(vectors)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    noise_count = int(np.sum(labels == -1))

    return labels, {
        "n_clusters": n_clusters,
        "noise_count": noise_count,
        "noise_ratio": noise_count / len(labels) if len(labels) > 0 else 0,
        "probabilities": clusterer.probabilities_.tolist(),
    }
