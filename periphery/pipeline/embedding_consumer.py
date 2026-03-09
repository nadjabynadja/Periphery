"""Embedding consumer — drives documents from enriched to embedded.

Claims enriched documents, generates semantic and entity-aware embeddings,
stores them in both SQLite (durable backup) and FAISS (query-time interface).
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite
import numpy as np
import structlog

from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore

from .consumer import StageConsumer

logger = structlog.get_logger(__name__)


class EmbeddingConsumer(StageConsumer):
    """Processes documents from enriched -> embedding -> embedded."""

    input_status = "enriched"
    processing_status = "embedding"
    output_status = "embedded"
    started_at_column = "embedding_started_at"
    completed_at_column = "embedding_completed_at"
    batch_size = 20

    def __init__(
        self,
        db_path: str,
        faiss_store: FAISSStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(db_path, **kwargs)
        self._store = faiss_store

    def set_store(self, store: FAISSStore) -> None:
        """Set the FAISS store (for deferred initialization)."""
        self._store = store

    async def process(
        self, db: aiosqlite.Connection, doc_rows: list[dict[str, Any]]
    ) -> list[str]:
        """Generate embeddings for claimed documents."""
        if self._store is None:
            logger.warning("faiss_store_not_configured")
            return []

        success_ids: list[str] = []
        semantic_texts: list[str] = []
        entity_texts: list[str] = []
        valid_docs: list[dict[str, Any]] = []

        for doc_row in doc_rows:
            doc_id = doc_row["id"]
            try:
                content = doc_row.get("content", "") or ""
                enrichment = await self._load_enrichment(db, doc_id)

                semantic_texts.append(content)
                entity_texts.append(self._build_entity_text(enrichment))
                valid_docs.append(doc_row)
            except Exception:
                logger.exception("embedding_prep_failed", doc_id=doc_id)

        if not valid_docs:
            return []

        try:
            # Generate embeddings in batch
            semantic_vectors = embedder.embed(semantic_texts)
            entity_vectors = embedder.embed(entity_texts)

            model_name = embedder.get_model().get_sentence_embedding_dimension.__qualname__
            dim = semantic_vectors.shape[1]

            # Store in SQLite and FAISS
            faiss_ids: list[str] = []
            faiss_vectors: list[np.ndarray] = []

            for i, doc_row in enumerate(valid_docs):
                doc_id = doc_row["id"]
                try:
                    await self._store_embedding(
                        db,
                        doc_id,
                        semantic_vectors[i],
                        entity_vectors[i],
                        dim,
                    )
                    faiss_ids.append(doc_id)
                    faiss_vectors.append(semantic_vectors[i])
                    success_ids.append(doc_id)
                except Exception:
                    logger.exception("embedding_store_failed", doc_id=doc_id)

            # Upsert into FAISS index
            if faiss_ids:
                vectors_array = np.stack(faiss_vectors).astype(np.float32)
                self._store.add(faiss_ids, vectors_array)
                logger.info(
                    "embeddings_added_to_faiss",
                    count=len(faiss_ids),
                    total=self._store.total,
                )

        except Exception:
            logger.exception("batch_embedding_failed")
            return []

        return success_ids

    async def _load_enrichment(
        self, db: aiosqlite.Connection, doc_id: str
    ) -> dict[str, Any]:
        """Load enrichment data for a document."""
        cursor = await db.execute(
            "SELECT entities, relationships, temporal_context, geospatial_data, cross_references "
            "FROM document_enrichments WHERE document_id = ?",
            (doc_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {}

        result: dict[str, Any] = {}
        col_names = ["entities", "relationships", "temporal_context", "geospatial_data", "cross_references"]
        for i, name in enumerate(col_names):
            val = row[i]
            if val and isinstance(val, str):
                try:
                    result[name] = json.loads(val)
                except json.JSONDecodeError:
                    result[name] = None
            else:
                result[name] = val
        return result

    def _build_entity_text(self, enrichment: dict[str, Any]) -> str:
        """Build a structured text representation of entities and relationships.

        E.g.: "PERSON: Mohammed bin Salman | ORG: Saudi Aramco | RELATIONSHIP: directs"
        """
        parts: list[str] = []

        entities = enrichment.get("entities") or []
        for ent in entities:
            if isinstance(ent, dict):
                etype = ent.get("entity_type", "ENTITY")
                text = ent.get("text", "")
                if text:
                    parts.append(f"{etype}: {text}")

        relationships = enrichment.get("relationships") or []
        for rel in relationships:
            if isinstance(rel, dict):
                subj = rel.get("subject_id", "")
                pred = rel.get("predicate", "")
                obj = rel.get("object_id", "")
                if pred:
                    parts.append(f"RELATIONSHIP: {subj} {pred} {obj}")

        return " | ".join(parts) if parts else "no entities extracted"

    async def _store_embedding(
        self,
        db: aiosqlite.Connection,
        doc_id: str,
        semantic_vec: np.ndarray,
        entity_vec: np.ndarray,
        dim: int,
    ) -> None:
        """Write embeddings to document_embeddings table."""
        await db.execute(
            """
            INSERT OR REPLACE INTO document_embeddings
                (document_id, semantic_embedding, entity_embedding,
                 embedding_model, embedding_dimensions)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                semantic_vec.tobytes(),
                entity_vec.tobytes(),
                "all-MiniLM-L6-v2",
                dim,
            ),
        )
        await db.commit()
