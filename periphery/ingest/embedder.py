import numpy as np
from sentence_transformers import SentenceTransformer

from periphery.config import get_settings

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        settings = get_settings()
        _model = SentenceTransformer(settings.embedding_model, device=settings.device)
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of texts into normalized vectors."""
    model = get_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.array(vectors, dtype=np.float32)


def get_dimension() -> int:
    """Return the embedding dimension."""
    return get_model().get_sentence_embedding_dimension()
