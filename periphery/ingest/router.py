import uuid

from fastapi import APIRouter, UploadFile, File, Form, Depends

from periphery.models import (
    Document, IngestRequest, IngestBatchRequest, IngestResponse,
    SearchRequest, SearchResult,
)
from periphery.ingest import embedder, parsers
from periphery.ingest.store import FAISSStore

router = APIRouter(prefix="/ingest", tags=["ingest"])

# These get set by main.py on startup
_store: FAISSStore | None = None
_documents: dict[str, Document] = {}


def set_store(store: FAISSStore) -> None:
    global _store
    _store = store


def get_store() -> FAISSStore:
    assert _store is not None, "Store not initialized"
    return _store


def get_documents() -> dict[str, Document]:
    return _documents


@router.post("/", response_model=IngestResponse)
async def ingest_document(request: IngestRequest):
    """Ingest a single document into the embedding space."""
    store = get_store()
    chunks = parsers.parse(request.content, request.content_type)

    doc_ids = []
    texts = []
    for chunk in chunks:
        doc_id = str(uuid.uuid4())
        doc = Document(id=doc_id, content=chunk, metadata=request.metadata)
        _documents[doc_id] = doc
        doc_ids.append(doc_id)
        texts.append(chunk)

    if texts:
        vectors = embedder.embed(texts)
        store.add(doc_ids, vectors)
        store.save()

    return IngestResponse(document_ids=doc_ids, count=len(doc_ids))


@router.post("/batch", response_model=IngestResponse)
async def ingest_batch(request: IngestBatchRequest):
    """Ingest multiple documents at once."""
    store = get_store()
    all_ids = []
    all_texts = []

    for item in request.documents:
        chunks = parsers.parse(item.content, item.content_type)
        for chunk in chunks:
            doc_id = str(uuid.uuid4())
            doc = Document(id=doc_id, content=chunk, metadata=item.metadata)
            _documents[doc_id] = doc
            all_ids.append(doc_id)
            all_texts.append(chunk)

    if all_texts:
        vectors = embedder.embed(all_texts)
        store.add(all_ids, vectors)
        store.save()

    return IngestResponse(document_ids=all_ids, count=len(all_ids))


@router.post("/upload", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    content_type: str = Form(None),
):
    """Ingest a file upload."""
    raw = await file.read()
    text = raw.decode("utf-8", errors="replace")
    ct = content_type or file.content_type or "text/plain"

    request = IngestRequest(content=text, content_type=ct, metadata={"filename": file.filename})
    return await ingest_document(request)


@router.post("/search", response_model=list[SearchResult])
async def search(request: SearchRequest):
    """Search the embedding space by natural language query."""
    from periphery.query.router import get_engine

    engine = get_engine()
    query_vec = embedder.embed([request.query])
    results = engine.store.search(query_vec[0], top_k=request.top_k)

    sources = []
    for doc_id, score in results:
        doc = await engine._resolve_document(doc_id)
        if doc:
            sources.append(SearchResult(document=doc, score=float(score)))
    return sources


@router.get("/stats")
async def stats():
    """Return store statistics."""
    store = get_store()
    return {
        "total_documents": len(_documents),
        "total_vectors": store.total,
        "embedding_dim": store.dim,
    }
