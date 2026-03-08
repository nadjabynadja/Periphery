import os
import pickle
from pathlib import Path

import faiss
import numpy as np


class FAISSStore:
    """FAISS-backed vector store with string ID mapping."""

    def __init__(self, dim: int, index_path: str = "data/faiss/index.bin"):
        self.dim = dim
        self.index_path = index_path
        self.id_map_path = index_path + ".ids"
        self.id_to_pos: dict[str, int] = {}
        self.pos_to_id: dict[int, str] = {}

        if os.path.exists(index_path) and os.path.exists(self.id_map_path):
            self.load()
        else:
            # Inner product index — works as cosine similarity with normalized vectors
            self.index = faiss.IndexFlatIP(dim)

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        """Add vectors with string IDs to the index."""
        assert vectors.shape[1] == self.dim
        assert len(ids) == vectors.shape[0]

        start_pos = self.index.ntotal
        self.index.add(vectors.astype(np.float32))

        for i, doc_id in enumerate(ids):
            pos = start_pos + i
            self.id_to_pos[doc_id] = pos
            self.pos_to_id[pos] = doc_id

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[tuple[str, float]]:
        """Search for nearest neighbors. Returns list of (doc_id, score)."""
        if self.index.ntotal == 0:
            return []

        query = query_vector.reshape(1, -1).astype(np.float32)
        k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            doc_id = self.pos_to_id.get(int(idx))
            if doc_id:
                results.append((doc_id, float(score)))
        return results

    def get_all_vectors(self) -> np.ndarray:
        """Reconstruct all vectors from the index."""
        if self.index.ntotal == 0:
            return np.empty((0, self.dim), dtype=np.float32)
        return faiss.rev_swig_ptr(
            self.index.get_xb(), self.index.ntotal * self.dim
        ).reshape(self.index.ntotal, self.dim).copy()

    def get_vectors_by_ids(self, ids: list[str]) -> np.ndarray:
        """Get vectors for specific document IDs."""
        positions = [self.id_to_pos[doc_id] for doc_id in ids if doc_id in self.id_to_pos]
        if not positions:
            return np.empty((0, self.dim), dtype=np.float32)
        all_vecs = self.get_all_vectors()
        return all_vecs[positions]

    def get_all_ids(self) -> list[str]:
        """Return all stored document IDs in index order."""
        return [self.pos_to_id[i] for i in range(self.index.ntotal) if i in self.pos_to_id]

    @property
    def total(self) -> int:
        return self.index.ntotal

    def save(self) -> None:
        """Persist index and ID mapping to disk."""
        Path(self.index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, self.index_path)
        with open(self.id_map_path, "wb") as f:
            pickle.dump((self.id_to_pos, self.pos_to_id), f)

    def load(self) -> None:
        """Load index and ID mapping from disk."""
        self.index = faiss.read_index(self.index_path)
        with open(self.id_map_path, "rb") as f:
            self.id_to_pos, self.pos_to_id = pickle.load(f)
