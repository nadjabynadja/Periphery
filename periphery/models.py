from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class Cluster(BaseModel):
    id: int
    document_ids: list[str]
    label: str | None = None
    coherence_score: float | None = None


class GraphNode(BaseModel):
    id: str
    label: str
    cluster_id: int | None = None
    coherence_score: float | None = None
    node_type: str = "document"  # "document" or "cluster"


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float


class OntologySnapshot(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    cluster_count: int = 0
    document_count: int = 0


class IngestRequest(BaseModel):
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_type: str = "text/plain"


class IngestBatchRequest(BaseModel):
    documents: list[IngestRequest]


class IngestResponse(BaseModel):
    document_ids: list[str]
    count: int


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


class SearchResult(BaseModel):
    document: Document
    score: float


class QueryRequest(BaseModel):
    question: str
    top_k: int = 10


class QueryResponse(BaseModel):
    answer: str
    sources: list[SearchResult]
    confidence: float
    graph_context: OntologySnapshot | None = None


class CriticScore(BaseModel):
    structure_id: str
    structure_type: str
    confidence: float
    confidence_raw: float
    confidence_calibrated: float
    signal_scores: dict[str, float]
