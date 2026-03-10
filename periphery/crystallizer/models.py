"""Data models for the Crystallizer engine.

Defines all structural observations: clusters, trajectories, relational
gradients, anomalies, and the composite living ontology snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Cluster Models ───────────────────────────────────────────────────────


class ClusterCorrelation(BaseModel):
    """Cross-space correlation for a detected cluster."""

    semantic: Optional[str] = None
    entity: Optional[str] = None
    relational: Optional[str] = None
    temporal: Optional[str] = None
    geospatial: Optional[str] = None


class DetectedCluster(BaseModel):
    """A cluster detected in a single embedding space."""

    cluster_id: str
    primary_space: str
    correlated_clusters: ClusterCorrelation = Field(default_factory=ClusterCorrelation)
    cross_space_coherence: float = 0.0
    member_document_ids: list[str] = Field(default_factory=list)
    size: int = 0
    density: float = 0.0
    stability: float = 1.0
    centroid: list[float] = Field(default_factory=list)
    label: str = ""
    status: str = "forming"  # forming | stable | growing | shrinking | dissolved
    key_entities: list[str] = Field(default_factory=list)
    key_relationships: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    temporal_center: Optional[datetime] = None
    geographic_center: Optional[dict[str, float]] = None


class SizeSnapshot(BaseModel):
    """A point-in-time size measurement for a cluster."""

    timestamp: datetime
    size: int


class ClusterHistory(BaseModel):
    """Lifecycle history for a persistent cluster."""

    cluster_id: str
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    size_history: list[SizeSnapshot] = Field(default_factory=list)
    status: str = "forming"
    split_from: Optional[str] = None
    merged_into: Optional[str] = None


# ── Trajectory Models ────────────────────────────────────────────────────


class TrajectorySnapshot(BaseModel):
    """A single centroid position snapshot for trajectory fitting."""

    timestamp: datetime
    centroid: list[float]


class Trajectory(BaseModel):
    """Directional movement of a cluster through embedding space."""

    trajectory_id: str
    cluster_id: str
    space: str
    direction_vector: list[float] = Field(default_factory=list)
    velocity: float = 0.0
    acceleration: float = 0.0
    confidence: float = 0.0
    pattern: str = "stable"  # convergence | divergence | acceleration | emergence | stable
    converging_with: Optional[str] = None
    first_detected: datetime = Field(default_factory=_utcnow)
    snapshots: list[TrajectorySnapshot] = Field(default_factory=list)


# ── Relational Gradient Models ───────────────────────────────────────────


class GradientComponents(BaseModel):
    """Component scores for a relational gradient between clusters."""

    entity_co_membership: float = 0.0
    temporal_alignment: float = 0.0
    geographic_proximity: float = 0.0
    relational_bridges: int = 0
    bridge_entities: list[str] = Field(default_factory=list)


class RelationalGradient(BaseModel):
    """An emergent relationship between two clusters."""

    source_cluster: str
    target_cluster: str
    gradient_score: float = 0.0
    components: GradientComponents = Field(default_factory=GradientComponents)
    first_detected: datetime = Field(default_factory=_utcnow)
    gradient_trend: str = "stable"  # strengthening | stable | weakening


# ── Anomaly Models ───────────────────────────────────────────────────────


class Anomaly(BaseModel):
    """A data point that doesn't fit any existing pattern."""

    anomaly_id: str
    document_id: str
    anomaly_type: str  # novel_entity | novel_relationship | geographic | temporal | structural
    anomaly_score: float = 0.0
    outlier_spaces: list[str] = Field(default_factory=list)
    nearest_cluster: str = ""
    distance_to_nearest: float = 0.0
    source_credibility: int = 4
    first_detected: datetime = Field(default_factory=_utcnow)
    resolved: bool = False
    resolved_into_cluster: Optional[str] = None
    description: str = ""


# ── Emerging Structure Models ────────────────────────────────────────────


class EmergingStructure(BaseModel):
    """A region of embedding space where a cluster may be forming."""

    region_id: str
    space: str
    centroid: list[float] = Field(default_factory=list)
    density: float = 0.0
    density_trend: str = "stable"  # increasing | stable | decreasing
    candidate_entities: list[str] = Field(default_factory=list)
    formation_confidence: float = 0.0
    label: str = ""
    detected_at: Optional[datetime] = Field(default_factory=_utcnow)


class ConvergenceAlert(BaseModel):
    """An alert that two clusters are moving toward each other."""

    cluster_a: str
    cluster_b: str
    convergence_rate: float = 0.0
    estimated_merge_time: Optional[datetime] = None
    significance: str = ""


# ── Living Ontology Snapshot ─────────────────────────────────────────────


class CorpusStats(BaseModel):
    """Corpus-level statistics for a snapshot."""

    total_documents: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    documents_since_last_snapshot: int = 0


class LivingOntologySnapshot(BaseModel):
    """The Crystallizer's composite output — a queryable state of the world."""

    snapshot_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    corpus_stats: CorpusStats = Field(default_factory=CorpusStats)
    clusters: list[DetectedCluster] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    emerging_structures: list[EmergingStructure] = Field(default_factory=list)
    convergence_alerts: list[ConvergenceAlert] = Field(default_factory=list)
    trajectories: list[Trajectory] = Field(default_factory=list)
    relational_gradients: list[RelationalGradient] = Field(default_factory=list)
    processing_time_ms: int = 0
