"""Pydantic models for the enrichment pipeline.

Defines the data structures that flow through every stage — from raw
IngestedDocument to fully enriched output ready for the embedding layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Stage 1: Entity Extraction ───────────────────────────────────────────


class ExtractedEntity(BaseModel):
    """A single entity extracted from a document."""

    text: str
    entity_type: str
    start_char: int
    end_char: int
    confidence: float
    extraction_method: str  # "spacy" or "pattern"
    context_window: str  # surrounding sentence


# ── Stage 2: Relationship Extraction ─────────────────────────────────────


class ExtractedRelationship(BaseModel):
    """A relationship between two entities."""

    subject_text: str
    subject_type: str
    subject_canonical_id: Optional[str] = None
    predicate: str
    object_text: str
    object_type: str
    object_canonical_id: Optional[str] = None
    confidence: float
    extraction_tier: int  # 1=co-occurrence, 2=dependency, 3=LLM
    extraction_method: str = ""  # co_occurrence | dependency_parse | llm
    temporal_qualifier: str = ""  # current | historical | speculative | unresolved
    evidence: str = ""
    implicit: bool = False
    co_occurrence_weight: Optional[float] = None  # only for Tier 1


# ── Stage 3: Temporal Tagging ────────────────────────────────────────────


class TemporalContext(BaseModel):
    """Temporal metadata attached to entities and relationships."""

    status: str  # current | historical | speculative | unresolved
    explicit_date: Optional[datetime] = None
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None
    document_date: Optional[datetime] = None
    tense_confidence: float = 0.0


# ── Stage 4: Geospatial Resolution ───────────────────────────────────────


class GeoCandidate(BaseModel):
    """A candidate geocoding result for ambiguous locations."""

    latitude: float
    longitude: float
    display_name: str
    confidence: float
    population: Optional[int] = None


class GeoHierarchy(BaseModel):
    """Hierarchical geographic containment."""

    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    continent: Optional[str] = None


class BoundingBox(BaseModel):
    """Geographic bounding box for area entities."""

    north: float
    south: float
    east: float
    west: float


class GeospatialData(BaseModel):
    """Geospatial metadata for location entities."""

    resolved: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    display_name: str = ""
    location_type: str = ""  # city | country | region | facility | maritime_chokepoint | etc.
    bounding_box: Optional[BoundingBox] = None
    hierarchy: GeoHierarchy = Field(default_factory=GeoHierarchy)
    confidence: float = 0.0
    geocoding_source: str = ""  # cache | geonames | nominatim
    candidates: list[GeoCandidate] = Field(default_factory=list)
    needs_crystallizer_resolution: bool = False
    geocoding_pending: bool = False

    # Legacy aliases for backward compatibility
    @property
    def resolution_confidence(self) -> float:
        return self.confidence

    @property
    def geo_candidates(self) -> list[GeoCandidate]:
        return self.candidates

    @property
    def geo_source(self) -> str:
        return self.geocoding_source


class RelationshipGeospatial(BaseModel):
    """Spatial metadata for relationships between geocoded entities."""

    distance_km: Optional[float] = None
    cross_border: bool = False
    subject_country: Optional[str] = None
    object_country: Optional[str] = None
    chokepoint_proximity: Optional[str] = None  # nearest chokepoint if path crosses one


class DocumentGeospatialSummary(BaseModel):
    """Document-level geospatial summary."""

    locations_found: int = 0
    locations_resolved: int = 0
    geographic_centroid: Optional[dict] = None  # {"lat": float, "lon": float}
    geographic_spread_km: Optional[float] = None
    primary_region: Optional[str] = None
    countries_referenced: list[str] = Field(default_factory=list)


# ── Stage 5: Source Credibility ──────────────────────────────────────────


class SourceCredibility(BaseModel):
    """Source credibility metadata."""

    source_credibility_tier: int  # 1-4
    source_name: str
    source_url: str
    source_category: str


# ── Stage 6: Entity Resolution ──────────────────────────────────────────


class CanonicalEntity(BaseModel):
    """A resolved canonical entity in the entity index."""

    canonical_id: str
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    source_documents: list[str] = Field(default_factory=list)
    credibility_floor: int = 4
    merge_confidence: float = 1.0


# ── Enriched Entity (post-pipeline) ─────────────────────────────────────


class EnrichedEntity(BaseModel):
    """An entity after all enrichment stages."""

    canonical_id: str = ""
    text: str
    entity_type: str
    confidence: float
    temporal_context: Optional[TemporalContext] = None
    geospatial: Optional[GeospatialData] = None
    credibility_tier: int = 4


# ── Enriched Relationship (post-pipeline) ────────────────────────────────


class EnrichedRelationship(BaseModel):
    """A relationship after all enrichment stages."""

    subject_id: str  # canonical entity ID
    predicate: str
    object_id: str  # canonical entity ID
    confidence: float
    extraction_tier: int
    extraction_method: str = ""
    temporal_context: Optional[TemporalContext] = None
    temporal_qualifier: str = ""
    evidence: str = ""
    implicit: bool = False
    co_occurrence_weight: Optional[float] = None
    geospatial: Optional[RelationshipGeospatial] = None
    credibility_tier: int = 4


# ── Enriched Document (final output) ────────────────────────────────────


class EnrichedDocumentSource(BaseModel):
    """Source metadata for an enriched document."""

    feed_url: str
    source_name: str
    source_category: str
    credibility_tier: int = 4


class EnrichedDocumentContent(BaseModel):
    """Content fields of an enriched document."""

    title: str
    full_text: str
    url: str
    published: Optional[datetime] = None
    ingested: datetime = Field(default_factory=_utcnow)


class EnrichmentMetadata(BaseModel):
    """Pipeline metadata about the enrichment process."""

    enrichment_stages_completed: list[str] = Field(default_factory=list)
    enrichment_failures: list[str] = Field(default_factory=list)
    processing_time_ms: int = 0
    llm_enrichment_status: str = "skipped"  # pending | complete | skipped | budget_exhausted
    relationship_counts: dict[str, int] = Field(default_factory=dict)  # tier -> count


class EnrichedDocument(BaseModel):
    """The final enriched document handed to the embedding layer."""

    id: str
    source: EnrichedDocumentSource
    content: EnrichedDocumentContent
    entities: list[EnrichedEntity] = Field(default_factory=list)
    relationships: list[EnrichedRelationship] = Field(default_factory=list)
    document_geospatial: Optional[DocumentGeospatialSummary] = None
    metadata: EnrichmentMetadata = Field(default_factory=EnrichmentMetadata)


# ── Pipeline Internal: document flowing through stages ───────────────────


class PipelineDocument(BaseModel):
    """Internal document representation flowing through pipeline stages.

    Accumulates enrichment results as it passes through each stage.
    Gets transformed into EnrichedDocument at the end.
    """

    model_config = {"arbitrary_types_allowed": True}

    id: str
    source_feed: str
    source_name: str = ""
    source_category: str
    title: str
    url: str
    full_text: str
    published: Optional[datetime] = None
    ingested: datetime = Field(default_factory=_utcnow)

    # Stage outputs accumulate here
    extracted_entities: list[ExtractedEntity] = Field(default_factory=list)
    extracted_relationships: list[ExtractedRelationship] = Field(default_factory=list)
    temporal_contexts: dict[str, TemporalContext] = Field(default_factory=dict)
    geospatial_data: dict[str, GeospatialData] = Field(default_factory=dict)
    document_geospatial: Optional[DocumentGeospatialSummary] = None
    relationship_geospatial: dict[str, RelationshipGeospatial] = Field(default_factory=dict)
    source_credibility: Optional[SourceCredibility] = None
    resolved_entity_map: dict[str, str] = Field(default_factory=dict)

    # SpaCy Doc object — shared between entity extraction and relationship extraction
    spacy_doc: Any = None

    # Crystallizer flag — documents flagged by the crystallizer get all tiers
    crystallizer_priority_flag: bool = False

    # LLM enrichment status tracking for async Tier 3
    llm_enrichment_status: str = "skipped"  # pending | complete | skipped | budget_exhausted

    # Pipeline metadata
    enrichment_stages_completed: list[str] = Field(default_factory=list)
    enrichment_failures: list[str] = Field(default_factory=list)
    priority: int = 3
