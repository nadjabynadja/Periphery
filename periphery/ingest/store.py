from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Embedding space names and their index types
EMBEDDING_SPACES: dict[str, str] = {
    "semantic": "ip",       # Inner product (cosine with normalized vectors)
    "entity": "ip",
    "relational": "ip",
    "temporal": "l2",       # Euclidean distance for coordinate spaces
    "geospatial": "l2",
}


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
        # Use JSON (not pickle) to avoid arbitrary code execution on load
        id_map_data = {
            "id_to_pos": self.id_to_pos,
            "pos_to_id": {str(k): v for k, v in self.pos_to_id.items()},
        }
        with open(self.id_map_path, "w", encoding="utf-8") as f:
            json.dump(id_map_data, f)

    def load(self) -> None:
        """Load index and ID mapping from disk."""
        self.index = faiss.read_index(self.index_path)
        # Use JSON (not pickle) to avoid arbitrary code execution on load
        with open(self.id_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.id_to_pos = {k: int(v) for k, v in data["id_to_pos"].items()}
        self.pos_to_id = {int(k): v for k, v in data["pos_to_id"].items()}


class MultiSpaceIndexManager:
    """Manages multiple FAISS indices for different embedding spaces.

    Each embedding space (semantic, entity, relational, temporal, geospatial)
    gets its own FAISS index with appropriate distance metric. The manager
    handles persistence, incremental updates, and periodic rebuilds.
    """

    def __init__(
        self,
        index_dir: str = "./data/indices",
        *,
        rebuild_interval: int = 10_000,
    ) -> None:
        self._index_dir = Path(index_dir)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._rebuild_interval = rebuild_interval
        self._docs_since_rebuild: int = 0

        # Per-space FAISS indices
        self._indices: dict[str, faiss.Index] = {}
        # Per-space ID mappings (bidirectional)
        self._id_maps: dict[str, dict[str, dict[str, int] | dict[int, str]]] = {}
        # Per-space dimensions (set on first add or load)
        self._dims: dict[str, int] = {}

    @property
    def spaces(self) -> list[str]:
        return list(EMBEDDING_SPACES.keys())

    def _index_path(self, space: str) -> str:
        return str(self._index_dir / f"{space}.index")

    def _id_map_path(self, space: str) -> str:
        return str(self._index_dir / f"{space}.index.ids")

    def _create_index(self, space: str, dim: int) -> faiss.Index:
        """Create a new FAISS index with the appropriate metric."""
        metric = EMBEDDING_SPACES.get(space, "ip")
        if metric == "l2":
            return faiss.IndexFlatL2(dim)
        return faiss.IndexFlatIP(dim)

    def initialize(self, dimensions: dict[str, int]) -> None:
        """Initialize or load indices for all spaces with given dimensions."""
        for space, dim in dimensions.items():
            self._dims[space] = dim
            idx_path = self._index_path(space)
            map_path = self._id_map_path(space)

            if os.path.exists(idx_path) and os.path.exists(map_path):
                try:
                    self._indices[space] = faiss.read_index(idx_path)
                    # Use JSON (not pickle) to avoid arbitrary code execution on load
                    with open(map_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    id_to_pos = {k: int(v) for k, v in data["id_to_pos"].items()}
                    pos_to_id = {int(k): v for k, v in data["pos_to_id"].items()}
                    self._id_maps[space] = {
                        "id_to_pos": id_to_pos,
                        "pos_to_id": pos_to_id,
                    }
                    logger.info(
                        "index_loaded",
                        space=space,
                        dim=dim,
                        vectors=self._indices[space].ntotal,
                    )
                    continue
                except Exception:
                    logger.exception("index_load_failed", space=space)

            # Create fresh index
            self._indices[space] = self._create_index(space, dim)
            self._id_maps[space] = {"id_to_pos": {}, "pos_to_id": {}}
            logger.info("index_created", space=space, dim=dim)

    def add(self, space: str, doc_ids: list[str], vectors: np.ndarray) -> None:
        """Add vectors to a specific embedding space index."""
        if space not in self._indices:
            raise ValueError(f"Unknown embedding space: {space}")
        if vectors.shape[0] == 0:
            return

        index = self._indices[space]
        maps = self._id_maps[space]
        id_to_pos: dict[str, int] = maps["id_to_pos"]
        pos_to_id: dict[int, str] = maps["pos_to_id"]

        start_pos = index.ntotal
        index.add(vectors.astype(np.float32))

        for i, doc_id in enumerate(doc_ids):
            pos = start_pos + i
            id_to_pos[doc_id] = pos
            pos_to_id[pos] = doc_id

    def search(
        self, space: str, query_vector: np.ndarray, top_k: int = 10
    ) -> list[tuple[str, float]]:
        """Search a specific embedding space. Returns (doc_id, score) pairs."""
        if space not in self._indices:
            return []
        index = self._indices[space]
        if index.ntotal == 0:
            return []

        pos_to_id = self._id_maps[space]["pos_to_id"]
        query = query_vector.reshape(1, -1).astype(np.float32)
        k = min(top_k, index.ntotal)
        scores, indices = index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            doc_id = pos_to_id.get(int(idx))
            if doc_id:
                results.append((doc_id, float(score)))
        return results

    def get_all_vectors(self, space: str) -> np.ndarray:
        """Get all vectors from a specific space."""
        if space not in self._indices:
            return np.empty((0, 0), dtype=np.float32)
        index = self._indices[space]
        dim = self._dims[space]
        if index.ntotal == 0:
            return np.empty((0, dim), dtype=np.float32)
        return faiss.rev_swig_ptr(
            index.get_xb(), index.ntotal * dim
        ).reshape(index.ntotal, dim).copy()

    def get_all_ids(self, space: str) -> list[str]:
        """Return all document IDs in a specific space's index."""
        if space not in self._indices:
            return []
        index = self._indices[space]
        pos_to_id = self._id_maps[space]["pos_to_id"]
        return [pos_to_id[i] for i in range(index.ntotal) if i in pos_to_id]

    def total(self, space: str) -> int:
        """Return total vector count for a space."""
        if space not in self._indices:
            return 0
        return self._indices[space].ntotal

    def stats(self) -> dict[str, Any]:
        """Return statistics for all embedding spaces."""
        result: dict[str, Any] = {}
        for space in EMBEDDING_SPACES:
            if space in self._indices:
                idx_path = self._index_path(space)
                file_size = os.path.getsize(idx_path) if os.path.exists(idx_path) else 0
                result[space] = {
                    "vectors": self._indices[space].ntotal,
                    "dimensions": self._dims.get(space, 0),
                    "metric": EMBEDDING_SPACES[space],
                    "file_size_bytes": file_size,
                }
            else:
                result[space] = {"vectors": 0, "dimensions": 0, "metric": EMBEDDING_SPACES[space]}
        result["docs_since_rebuild"] = self._docs_since_rebuild
        result["rebuild_interval"] = self._rebuild_interval
        return result

    def save(self) -> None:
        """Persist all indices and ID mappings to disk."""
        self._index_dir.mkdir(parents=True, exist_ok=True)
        for space in self._indices:
            idx_path = self._index_path(space)
            map_path = self._id_map_path(space)
            faiss.write_index(self._indices[space], idx_path)
            # Use JSON (not pickle) to avoid arbitrary code execution on load
            id_map_data = {
                "id_to_pos": self._id_maps[space]["id_to_pos"],
                "pos_to_id": {str(k): v for k, v in self._id_maps[space]["pos_to_id"].items()},
            }
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump(id_map_data, f)
        logger.info("all_indices_saved", spaces=list(self._indices.keys()))

    def save_space(self, space: str) -> None:
        """Persist a single space's index to disk."""
        if space not in self._indices:
            return
        idx_path = self._index_path(space)
        map_path = self._id_map_path(space)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._indices[space], idx_path)
        # Use JSON (not pickle) to avoid arbitrary code execution on load
        id_map_data = {
            "id_to_pos": self._id_maps[space]["id_to_pos"],
            "pos_to_id": {str(k): v for k, v in self._id_maps[space]["pos_to_id"].items()},
        }
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(id_map_data, f)

    def track_batch(self, batch_size: int) -> bool:
        """Track documents added since last rebuild. Returns True if rebuild needed."""
        self._docs_since_rebuild += batch_size
        if self._docs_since_rebuild >= self._rebuild_interval:
            self._docs_since_rebuild = 0
            return True
        return False

    def rebuild_space(self, space: str, doc_ids: list[str], vectors: np.ndarray) -> None:
        """Rebuild an entire space index from scratch."""
        if space not in self._dims:
            return
        dim = self._dims[space]
        self._indices[space] = self._create_index(space, dim)
        self._id_maps[space] = {"id_to_pos": {}, "pos_to_id": {}}
        if vectors.shape[0] > 0:
            self.add(space, doc_ids, vectors)
        logger.info("index_rebuilt", space=space, vectors=vectors.shape[0])
