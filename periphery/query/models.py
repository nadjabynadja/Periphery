"""Data models for the Query Interface layer.

Defines structured intents, query plans, retrieval results, rendering
metadata, and session state for the natural language query pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Parsed Intent ────────────────────────────────────────────────────────


class GeographicScope(BaseModel):
    regions: list[str] = Field(default_factory=list)
    coordinates: Optional[dict[str, float]] = None  # lat, lon, radius_km
    scope_type: str = "global"  # specific_location | region | global


class TemporalScope(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    temporal_focus: str = "current"  # current | historical | trend | predictive


class ParsedIntent(BaseModel):
    query_type: str = "freeform"
    entities_referenced: list[str] = Field(default_factory=list)
    entity_types_requested: list[str] = Field(default_factory=list)
    relationships_requested: list[str] = Field(default_factory=list)
    geographic_scope: GeographicScope = Field(default_factory=GeographicScope)
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)
    confidence_threshold: float = 0.0
    analytical_focus: str = "connections"
    implied_subqueries: list[str] = Field(default_factory=list)
    clusters_likely_relevant: list[str] = Field(default_factory=list)


# ── Query Plan ───────────────────────────────────────────────────────────


class PlanOperation(BaseModel):
    operation_id: str
    type: str  # entity_search | cluster_retrieval | semantic_search | relational_path | geographic_filter | temporal_filter | trajectory_retrieval | anomaly_retrieval
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0


class QueryPlan(BaseModel):
    plan_id: str
    query_id: str
    operations: list[PlanOperation] = Field(default_factory=list)
    merge_strategy: str = "ranked_fusion"  # union | intersection | ranked_fusion


# ── Retrieval Results ────────────────────────────────────────────────────


class EntityResult(BaseModel):
    canonical_id: str
    name: str
    type: str
    confidence: float = 0.0
    confidence_explanation: dict[str, Any] = Field(default_factory=dict)
    cluster_memberships: list[str] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    temporal_context: dict[str, Any] = Field(default_factory=dict)
    geospatial: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = 0.0
    source_documents: list[str] = Field(default_factory=list)


class ClusterResult(BaseModel):
    cluster_id: str
    label: str = ""
    confidence: float = 0.0
    confidence_explanation: dict[str, Any] = Field(default_factory=dict)
    size: int = 0
    key_entities: list[dict[str, Any]] = Field(default_factory=list)
    key_relationships: list[dict[str, Any]] = Field(default_factory=list)
    trajectories: list[dict[str, Any]] = Field(default_factory=list)
    geographic_center: Optional[dict[str, float]] = None
    temporal_center: Optional[str] = None
    relevance_score: float = 0.0


class RelationshipResult(BaseModel):
    subject: dict[str, Any] = Field(default_factory=dict)
    predicate: str = ""
    object: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    confidence_explanation: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    temporal_context: dict[str, Any] = Field(default_factory=dict)
    extraction_tier: int = 1
    relevance_score: float = 0.0


class TrajectoryResult(BaseModel):
    trajectory_id: str
    cluster_label: str = ""
    pattern: str = "stable"
    velocity: float = 0.0
    confidence: float = 0.0
    description: str = ""
    extrapolation: Optional[dict[str, Any]] = None


class AnomalyResult(BaseModel):
    anomaly_id: str
    type: str = ""
    score: float = 0.0
    description: str = ""
    related_entities: list[str] = Field(default_factory=list)
    source_credibility: int = 4


class RelationalPath(BaseModel):
    from_entity: str
    to_entity: str
    path: list[dict[str, Any]] = Field(default_factory=list)
    path_confidence: float = 0.0
    path_type: str = "direct"  # direct | indirect | cluster_mediated


class EmergingStructureResult(BaseModel):
    region_id: str
    description: str = ""
    formation_confidence: float = 0.0
    candidate_entities: list[str] = Field(default_factory=list)


class RetrievalResults(BaseModel):
    query_id: str
    entities: list[EntityResult] = Field(default_factory=list)
    clusters: list[ClusterResult] = Field(default_factory=list)
    relationships: list[RelationshipResult] = Field(default_factory=list)
    trajectories: list[TrajectoryResult] = Field(default_factory=list)
    anomalies: list[AnomalyResult] = Field(default_factory=list)
    relational_paths: list[RelationalPath] = Field(default_factory=list)
    emerging_structures: list[EmergingStructureResult] = Field(default_factory=list)


# ── Synthesis Output ─────────────────────────────────────────────────────


class SynthesisOutput(BaseModel):
    summary: str = ""
    analysis: str = ""
    confidence_assessment: str = ""
    key_findings: list[str] = Field(default_factory=list)
    gaps_and_limitations: list[str] = Field(default_factory=list)
    suggested_followups: list[str] = Field(default_factory=list)
    sources_used: int = 0
    highest_confidence_finding: str = ""
    lowest_confidence_finding: str = ""


# ── Rendering Metadata ───────────────────────────────────────────────────


class RenderingMetadata(BaseModel):
    legibility_tier: str = "whisper"
    opacity: float = 0.15
    blur: int = 5
    animation: str = "slow_drift"
    border: str = "none"
    label_visibility: str = "on_click_only"
    confidence_color: str = "#3A4A5C"
    glow_intensity: float = 0.1


# ── Execution Stats ──────────────────────────────────────────────────────


class ExecutionStats(BaseModel):
    total_time_ms: int = 0
    intent_parsing_ms: int = 0
    planning_ms: int = 0
    retrieval_ms: int = 0
    synthesis_ms: int = 0
    operations_executed: int = 0
    documents_searched: int = 0
    cached: bool = False


# ── API Request/Response Models ──────────────────────────────────────────


class AnalyticalQueryRequest(BaseModel):
    query: str
    confidence_floor: float = 0.0
    max_results: int = 50
    include_sources: bool = True
    include_rendering: bool = True
    temporal_override: Optional[dict[str, Any]] = None
    geographic_override: Optional[dict[str, Any]] = None
    session_id: Optional[str] = None


class AnalyticalQueryResponse(BaseModel):
    query_id: str
    parsed_intent: ParsedIntent
    synthesis: SynthesisOutput
    results: dict[str, Any] = Field(default_factory=dict)
    execution_stats: ExecutionStats = Field(default_factory=ExecutionStats)


class SnapshotRequest(BaseModel):
    confidence_floor: float = 0.0
    cluster_ids: Optional[list[str]] = None
    geographic_bounds: Optional[dict[str, float]] = None
    temporal_range: Optional[dict[str, str]] = None


class StreamUpdate(BaseModel):
    update_type: str  # new_entity | confidence_change | new_relationship | new_anomaly | cluster_update | trajectory_update
    query_id: str
    timestamp: str = ""
    delta: dict[str, Any] = Field(default_factory=dict)


# ── Session State ────────────────────────────────────────────────────────


class SessionState(BaseModel):
    session_id: str
    previous_queries: list[dict[str, Any]] = Field(default_factory=list)
    bookmarked_entities: list[str] = Field(default_factory=list)
    confidence_preference: float = 0.0
    geographic_focus: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)
    last_active: datetime = Field(default_factory=_utcnow)
