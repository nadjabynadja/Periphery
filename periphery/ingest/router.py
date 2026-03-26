import logging
import uuid

from fastapi import APIRouter, UploadFile, File, Form, Depends

from periphery.models import (
    Document, IngestRequest, IngestBatchRequest, IngestResponse,
    SearchRequest, SearchResult,
)
from periphery.ingest import embedder, parsers
from periphery.ingest.store import FAISSStore
from periphery.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

# These get set by main.py on startup
_store: FAISSStore | None = None

# LEGACY: This in-memory dict stores documents ingested via the legacy /ingest/ endpoint.
# It is NOT persisted — all documents are lost on server restart.
# Used only by the legacy query engine and crystallizer store; the RSS pipeline uses the
# SQLite-backed DocumentStore instead.
# TODO: Replace with a database-backed store to prevent silent data loss on restart.
_documents: dict[str, Document] = {}
_legacy_warning_emitted = False


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
    """Ingest a single document into the embedding space.

    .. deprecated::
        This endpoint uses an in-memory document store that is **not persisted**.
        All documents submitted here are lost on server restart.
        Use the RSS pipeline or the database-backed ingest path instead.
    """
    global _legacy_warning_emitted
    if not _legacy_warning_emitted:
        logger.warning(
            "legacy_ingest_endpoint_used: /ingest/ stores documents in-memory only. "
            "Data will be lost on restart. Migrate to the database-backed pipeline."
        )
        _legacy_warning_emitted = True
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
    """Ingest multiple documents at once.

    .. deprecated::
        This endpoint uses an in-memory document store that is **not persisted**.
        All documents submitted here are lost on server restart.
        Use the RSS pipeline or the database-backed ingest path instead.
    """
    global _legacy_warning_emitted
    if not _legacy_warning_emitted:
        logger.warning(
            "legacy_ingest_batch_endpoint_used: /ingest/batch stores documents in-memory only. "
            "Data will be lost on restart. Migrate to the database-backed pipeline."
        )
        _legacy_warning_emitted = True
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
    """Return store statistics — combines RSS pipeline SQLite counts with FAISS index size."""
    store = get_store()

    # Query the RSS pipeline's SQLite document store for real counts
    rss_total = 0
    rss_by_status: dict = {}
    rss_last_hour = 0
    rss_last_day = 0
    try:
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            row = await cursor.fetchone()
            rss_total = row[0] if row else 0

            cursor = await db.execute(
                "SELECT processing_status, COUNT(*) FROM documents GROUP BY processing_status"
            )
            rss_by_status = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 hour')"
            )
            row = await cursor.fetchone()
            rss_last_hour = row[0] if row else 0

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 day')"
            )
            row = await cursor.fetchone()
            rss_last_day = row[0] if row else 0
    except Exception:
        pass  # pool may not be initialized in all test contexts

    return {
        "total_documents": rss_total,
        "total_vectors": store.total,
        "embedding_dim": store.dim,
        "processing_status_breakdown": rss_by_status,
        "ingested_last_hour": rss_last_hour,
        "ingested_last_day": rss_last_day,
        # Legacy: docs submitted via HTTP /ingest/ endpoint (not RSS pipeline)
        "legacy_http_ingest_count": len(_documents),
    }
