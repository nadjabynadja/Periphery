import os
import tempfile

import numpy as np
import pytest

from periphery.ingest.store import FAISSStore


@pytest.fixture
def tmp_store():
    """Create a temporary FAISS store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test.bin")
        store = FAISSStore(dim=384, index_path=path)
        yield store


@pytest.fixture
def sample_vectors():
    """Generate sample normalized vectors for testing."""
    rng = np.random.RandomState(42)
    vecs = rng.randn(20, 384).astype(np.float32)
    # Normalize
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / norms


@pytest.fixture
def sample_ids():
    return [f"doc_{i}" for i in range(20)]
