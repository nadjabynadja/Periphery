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
    from periphery.ingest.router import get_store, get_documents

    store = get_store()
    documents = get_documents()

    query_vec = embedder.embed([request.query])
    results = store.search(query_vec[0], top_k=request.top_k)

    return [
        SearchResult(document=documents[doc_id], score=score)
        for doc_id, score in results
        if doc_id in documents
    ]
