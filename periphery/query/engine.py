import json
import logging
from datetime import datetime, timezone

import anthropic

from periphery.config import get_settings
from periphery.crystallizer.graph import OntologyGraph
from periphery.db import get_pool
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore
from periphery.models import Document, QueryResponse, SearchResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the query interface for Periphery, a data infrastructure system where schema emerges from observation rather than being predefined.

You are answering questions about data that has been ingested into an embedding space and automatically organized through density-based clustering. The structure you see has emerged from the data itself — it was not authored by a human.

Context about the data:
- Documents were embedded into a high-dimensional vector space
- Clusters were detected automatically using density-based algorithms
- Each cluster and document has a coherence score indicating structural confidence
- Higher coherence scores (closer to 1.0) indicate stronger structural patterns
- Lower scores indicate emerging or uncertain patterns

When answering:
1. Ground your answers in the retrieved documents provided as context
2. Indicate confidence levels — distinguish between well-established patterns and emerging ones
3. If the data is insufficient to answer, say so clearly
4. Reference the coherence scores when discussing structural relationships
5. Do not fabricate information beyond what the context provides"""


class QueryEngine:
    """Natural language query engine combining vector search, graph context, and Claude."""

    def __init__(
        self,
        store: FAISSStore,
        documents: dict[str, Document],
        graph: OntologyGraph,
        coherence_scores: dict[int, float] | None = None,
        db_path: str | None = None,
    ):
        self.store = store
        self.documents = documents
        self.graph = graph
        self.coherence_scores = coherence_scores or {}
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
        self._db_path = db_path or settings.pipeline_db_path

    async def _fetch_document_from_db(self, doc_id: str) -> Document | None:
        """Fall back to the SQLite document store for pipeline-ingested documents."""
        try:
            pool = get_pool()
            async with pool.acquire() as db:
                cursor = await db.execute(
                    "SELECT id, content, title, url, metadata, published "
                    "FROM documents WHERE id = ?",
                    (doc_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None

                row_id, content, title, url, metadata_json, published = row
                metadata: dict = {}
                if metadata_json:
                    try:
                        metadata = json.loads(metadata_json)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if title:
                    metadata["title"] = title
                if url:
                    metadata["url"] = url

                created_at = datetime.now(timezone.utc)
                if published:
                    try:
                        created_at = datetime.fromisoformat(published)
                    except (ValueError, TypeError):
                        pass

                doc = Document(
                    id=row_id,
                    content=content or "",
                    metadata=metadata,
                    created_at=created_at,
                )
                # Cache in memory for subsequent lookups
                self.documents[doc_id] = doc
                return doc
        except Exception:
            logger.debug("sqlite_fallback_failed", exc_info=True)
            return None

    async def _resolve_document(self, doc_id: str) -> Document | None:
        """Look up a document by ID, falling back to SQLite if not in memory."""
        doc = self.documents.get(doc_id)
        if doc is not None:
            return doc
        return await self._fetch_document_from_db(doc_id)

    async def query(self, question: str, top_k: int = 10) -> QueryResponse:
        """Process a natural language query against the crystallized state."""
        # 1. Embed the question and search
        query_vec = embedder.embed([question])[0]
        search_results = self.store.search(query_vec, top_k=top_k)

        sources = []
        for doc_id, score in search_results:
            doc = await self._resolve_document(doc_id)
            if doc:
                sources.append(SearchResult(document=doc, score=float(score)))

        # 2. Expand context via graph neighbors
        graph_context = None
        if sources:
            top_doc_id = sources[0].document.id
            graph_context = self.graph.get_subgraph(top_doc_id, depth=1)

        # 3. Build context for Claude
        context_parts = []
        for i, sr in enumerate(sources):
            score_label = _confidence_label(sr.score)
            context_parts.append(
                f"[Document {i+1}] (relevance: {sr.score:.3f}, confidence: {score_label})\n{sr.document.content}"
            )

        if graph_context and graph_context.nodes:
            context_parts.append(
                f"\n[Graph Context] {graph_context.cluster_count} clusters, "
                f"{graph_context.document_count} connected documents"
            )

        context_text = "\n\n".join(context_parts)

        # 4. Call Claude API
        if self.client:
            try:
                response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Question: {question}\n\nRetrieved context:\n{context_text}",
                        }
                    ],
                )
                answer = response.content[0].text
            except Exception as e:
                logger.error("Claude API call failed: %s", e)
                answer = _fallback_answer(question, sources)
        else:
            answer = _fallback_answer(question, sources)

        # 5. Compute overall confidence
        if sources:
            avg_score = sum(sr.score for sr in sources) / len(sources)
            confidence = min(1.0, avg_score)
        else:
            confidence = 0.0

        return QueryResponse(
            answer=answer,
            sources=sources,
            confidence=confidence,
            graph_context=graph_context,
        )


def _confidence_label(score: float) -> str:
    if score > 0.8:
        return "high"
    elif score > 0.5:
        return "medium"
    elif score > 0.3:
        return "low"
    else:
        return "emerging"


def _fallback_answer(question: str, sources: list[SearchResult]) -> str:
    """Generate a basic answer without Claude API."""
    if not sources:
        return "No relevant documents found in the current data."

    parts = [f"Found {len(sources)} relevant documents:"]
    for i, sr in enumerate(sources[:5]):
        parts.append(f"\n{i+1}. (score: {sr.score:.3f}) {sr.document.content[:200]}")

    parts.append(
        "\n\n(Note: Claude API key not configured. "
        "Set ANTHROPIC_API_KEY for synthesized answers.)"
    )
    return "\n".join(parts)
