from fastapi import APIRouter

from periphery.models import (
    QueryRequest, QueryResponse, SearchRequest, SearchResult,
)
from periphery.ingest import embedder


router = APIRouter(prefix="/query", tags=["query"])

# Set by main.py
_engine = None


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def get_engine():
    assert _engine is not None, "Query engine not initialized"
    return _engine


@router.post("/", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Query the crystallized data with natural language."""
    engine = get_engine()
    return await engine.query(request.question, top_k=request.top_k)


@router.post("/similar", response_model=list[SearchResult])
async def find_similar(request: SearchRequest):
    """Find similar documents without Claude synthesis (pure vector search)."""
    engine = get_engine()

    query_vec = embedder.embed([request.query])
    results = engine.store.search(query_vec[0], top_k=request.top_k)

    sources = []
    for doc_id, score in results:
        doc = await engine._resolve_document(doc_id)
        if doc:
            sources.append(SearchResult(document=doc, score=float(score)))
    return sources
