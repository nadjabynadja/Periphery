// ============================================
// Periphery API Types
// Mirror of backend Pydantic models — compile-time safety
// ============================================

// --- Core Models ---

export interface Document {
  id: string
  content: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface SearchResult {
  document: Document
  score: number
}

// --- Ontology Graph (Legacy) ---

export interface GraphNode {
  id: string
  label: string
  cluster_id: number | null
  coherence_score: number | null
  node_type: string // "document" | "cluster"
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
}

export interface LegacyOntologySnapshot {
  nodes: GraphNode[]
  edges: GraphEdge[]
  cluster_count: number
  document_count: number
}

// --- Living Ontology Snapshot (from /api/snapshot) ---

export interface EntityNode {
  canonical_id: string
  name: string
  entity_type: string
  aliases: string[]
  confidence: number
  source_count: number
  cluster_ids: string[]
  first_seen: string
  last_seen: string
  location?: { lat: number; lon: number; name?: string }
  rendering: RenderingMetadata
}

export interface Relationship {
  id: string
  subject_id: string
  predicate: string
  object_id: string
  confidence: number
  evidence_sentences: string[]
  temporal_context: string // "current" | "historical" | "speculative"
  extraction_tier: string // "co_occurrence" | "dependency" | "llm"
  source_count: number
  first_seen: string
  last_seen: string
}

export interface DetectedCluster {
  cluster_id: string
  label: string
  status: string // "forming" | "stable" | "growing" | "shrinking"
  confidence: number
  member_ids?: string[]
  member_document_ids?: string[]
  key_entities: string[]
  coherence_score?: number
  cross_space_coherence?: number
  size?: number
  density?: number
  stability?: number
  centroid_position?: number[]
  centroid?: number[]
  geographic_footprint?: { lat: number; lon: number; name?: string }[]
  geographic_center?: { lat: number; lon: number } | null
  formed_at?: string
  last_updated?: string
  rendering?: RenderingMetadata
}

export interface Trajectory {
  trajectory_id: string
  cluster_id: string
  pattern: string
  description: string
  velocity: number
  snapshots: { timestamp: string; position: number[] }[]
}

export interface Anomaly {
  anomaly_id: string
  anomaly_type: string
  anomaly_score: number
  source_credibility: number
  flagging_spaces: string[]
  nearest_cluster_id: string | null
  nearest_cluster_distance: number | null
  source_document_id: string
  related_entity_ids: string[]
  description: string
  detected_at: string
}

export interface RelationalGradient {
  source_cluster_id: string
  target_cluster_id: string
  score: number
  relationship_count: number
  key_relationships: string[]
}

export interface EmergingStructure {
  structure_id: string
  member_ids: string[]
  formation_progress: number // 0-1
  potential_label: string
  detected_at: string
}

export interface RenderingMetadata {
  opacity: number
  blur: number
  glow_intensity: number
  glow_color: string
  border_style: string
  label_visibility: string
  pulse_animation: boolean
  pulse_speed: number
  size_multiplier: number
  tier: string // "solid" | "defined" | "emerging" | "haze" | "whisper"
}

export interface OntologySnapshot {
  entities?: EntityNode[]
  relationships?: Relationship[]
  clusters: DetectedCluster[]
  trajectories: Trajectory[]
  anomalies: Anomaly[]
  gradients: RelationalGradient[]
  emerging_structures: EmergingStructure[]
  timestamp: string
  total_entities?: number
  total_relationships?: number
  entity_count: number
  relationship_count: number
  cluster_count: number
}

export interface PaginatedEntities {
  total: number
  page: number
  limit: number
  entities: EntityNode[]
}

export interface PaginatedRelationships {
  total: number
  page: number
  limit: number
  relationships: Relationship[]
}

// --- Query Types ---

export interface QueryOptions {
  top_k?: number
  session_id?: string
  confidence_floor?: number
  geographic_filter?: string
  temporal_filter?: string
}

export interface AnalyticalQueryResponse {
  query_id: string
  parsed_intent: ParsedIntent
  synthesis: SynthesisOutput
  results: QueryResults
  execution_stats: ExecutionStats
  // Computed convenience accessors (populated by client adapter)
  narrative: string
  key_findings: Finding[]
  entities: EntityResult[]
  relationships: RelationshipResult[]
  clusters: ClusterResult[]
  trajectories: TrajectoryResult[]
  anomalies: AnomalyResult[]
  gaps: string[]
  suggested_followups: string[]
  confidence: number
  processing_time_ms: number
}

export interface SynthesisOutput {
  summary: string
  analysis: string
  confidence_assessment: string
  key_findings: string[]
  gaps_and_limitations: string[]
  suggested_followups: string[]
  sources_used: number
  highest_confidence_finding: string
  lowest_confidence_finding: string
}

export interface ExecutionStats {
  total_time_ms: number
  intent_parsing_ms: number
  planning_ms: number
  retrieval_ms: number
  synthesis_ms: number
  operations_executed: number
  documents_searched: number
  cached: boolean
}

export interface QueryResults {
  entities: EntityResult[]
  clusters: ClusterResult[]
  relationships: RelationshipResult[]
  trajectories: TrajectoryResult[]
  anomalies: AnomalyResult[]
  [key: string]: unknown
}

export interface ParsedIntent {
  query_type: string
  entities_referenced: string[]
  entity_types_requested: string[]
  relationships_requested: string[]
  geographic_scope?: Record<string, unknown>
  temporal_scope?: Record<string, unknown>
  confidence_threshold: number
  analytical_focus: string
  implied_subqueries: string[]
  clusters_likely_relevant: string[]
  // Aliases for backward compat in UI
  intent_type?: string
  entity_mentions?: string[]
}

export interface Finding {
  text: string
  confidence: number
  supporting_entity_ids: string[]
}

export interface EntityResult {
  canonical_id: string
  name: string
  entity_type: string
  confidence: number
  relevance_score: number
  source_count: number
  temporal_context: string
}

export interface RelationshipResult {
  subject_name: string
  predicate: string
  object_name: string
  confidence: number
  relevance_score: number
  evidence_snippet: string
}

export interface ClusterResult {
  cluster_id: string
  label: string
  confidence: number
  relevance_score: number
  member_count: number
}

export interface TrajectoryResult {
  trajectory_id: string
  cluster_label: string
  pattern: string
  velocity: number
}

export interface AnomalyResult {
  anomaly_id: string
  anomaly_type: string
  anomaly_score: number
  description: string
}

// --- Entity Detail ---

export interface EntityDetail {
  canonical_id: string
  name: string
  entity_type: string
  aliases: string[]
  confidence: number
  confidence_explanation: ConfidenceExplanation
  source_count: number
  cluster_memberships: ClusterMembership[]
  relationships: EntityRelationship[]
  temporal_history: TemporalDataPoint[]
  locations: { lat: number; lon: number; name?: string }[]
  source_documents: SourceDocument[]
  rendering: RenderingMetadata
}

export interface ConfidenceExplanation {
  overall_score: number
  factors: ConfidenceFactor[]
}

export interface ConfidenceFactor {
  name: string
  score: number
  weight: number
  description: string
}

export interface ClusterMembership {
  cluster_id: string
  label: string
  confidence: number
  role: string // "core" | "peripheral"
}

export interface EntityRelationship {
  relationship_id: string
  predicate: string
  other_entity_id: string
  other_entity_name: string
  direction: 'outgoing' | 'incoming'
  confidence: number
  temporal_context: string
  evidence_sentence: string
}

export interface TemporalDataPoint {
  date: string
  count: number
}

export interface SourceDocument {
  document_id: string
  title: string
  source: string
  date: string
  content_quality: string // "full" | "summary" | "metadata"
  snippet: string
}

// --- Cluster Detail ---

export interface ClusterDetail {
  cluster_id: string
  label: string
  status: string
  confidence: number
  confidence_explanation: ConfidenceExplanation
  key_entities: EntityResult[]
  internal_relationships: RelationshipResult[]
  external_connections: RelationalGradient[]
  trajectory: Trajectory | null
  timeline: ClusterTimelineEvent[]
  geographic_footprint: { lat: number; lon: number; name?: string }[]
  member_count: number
  formed_at: string
}

export interface ClusterTimelineEvent {
  timestamp: string
  event_type: string // "formed" | "grew" | "shrank" | "split" | "merged"
  description: string
}

// --- Pipeline Stats ---

export interface PipelineStats {
  stages: PipelineStage[]
  total_processed: number
  total_failed: number
  pipeline_lag_seconds: number
  uptime_seconds: number
}

export interface PipelineStage {
  name: string
  status: 'healthy' | 'degraded' | 'error'
  queue_size: number
  throughput_per_minute: number
  last_processed: string | null
  error_count: number
}

export interface EmbeddingStats {
  spaces: Record<string, { count: number; dimension: number }>
  completeness: Record<string, number>
}

// --- Health ---

export interface HealthStatus {
  status: string
  vectors: number
  clusters: number
  last_crystallization: string | null
  pipeline: boolean
}

// --- Critic ---

export interface CriticMonitoring {
  model_version: number
  mean_confidence: number
  score_distribution: Record<string, number>
  alerts: string[]
  last_training: string | null
  total_scored: number
}

export interface CriticExplanation {
  structure_id: string
  structure_type: string
  overall_score: number
  factors: ConfidenceFactor[]
  timestamp: string
}

export interface CriticScoreTrend {
  timestamps: string[]
  mean_scores: number[]
  high_count: number[]
  low_count: number[]
}

// --- WebSocket ---

export interface SnapshotDelta {
  type: 'snapshot_delta'
  added_entities: EntityNode[]
  removed_entity_ids: string[]
  updated_entities: EntityNode[]
  added_relationships: Relationship[]
  removed_relationship_ids: string[]
  added_anomalies: Anomaly[]
  resolved_anomaly_ids: string[]
  timestamp: string
}

export interface QueryUpdate {
  type: 'query_progress' | 'query_complete' | 'query_error'
  query_id: string
  progress?: number
  partial_result?: Partial<AnalyticalQueryResponse>
  error?: string
}

// --- Legibility Tiers ---

export type LegibilityTier = 'solid' | 'defined' | 'emerging' | 'haze' | 'whisper'

export interface NodeRenderState {
  opacity: number
  blur: number
  glowIntensity: number
  glowColor: string
  borderStyle: string
  labelVisibility: 'full' | 'on_hover' | 'on_click_only'
  pulseAnimation: boolean
  pulseSpeed: number
  size: number
  tier: LegibilityTier
}

// --- Query History ---

export interface QueryHistoryEntry {
  query_id: string
  query_text: string
  timestamp: string
  confidence: number
}

// --- Snapshot Filters ---

export interface SnapshotFilters {
  confidence_floor?: number
  cluster_ids?: string[]
  include_rendering?: boolean
}

// --- Connection Status ---

export type ConnectionStatus = 'connected' | 'reconnecting' | 'disconnected'

// --- Source Feed ---

export type SourceCategory = 'government' | 'news' | 'cyber' | 'academic' | 'conflict'

export interface FeedEntry {
  id: string
  title: string
  source: string
  category: SourceCategory
  timestamp: string
  content_quality: 'full' | 'summary' | 'metadata'
  confidence?: number
  entity_count?: number
}

// --- View Modes ---

export type ViewMode = 'graph' | 'map' | 'timeline'

// --- Search Types ---

export interface DocumentSearchResult {
  id: string
  title: string
  url: string
  source_feed: string
  source_category: string
  published: string
  processing_status: string
  content_quality: string
  snippet: string
  entity_count: number
  relationship_count: number
  relevance_score: number
}

export interface DocumentSearchResponse {
  results: DocumentSearchResult[]
  total_count: number
  offset: number
  query: string
}

export interface EntitySearchResult {
  entity_text: string
  entity_type: string
  confidence: number
  document_count: number
  source_feeds: string[]
  first_seen: string
  last_seen: string
  location: { lat: number; lon: number; name: string } | null
  relevance_score: number
}

export interface EntitySearchResponse {
  results: EntitySearchResult[]
  total_count: number
  offset: number
  query: string
}

export interface RelationshipSearchResult {
  subject_text: string
  predicate: string
  object_text: string
  confidence: number
  extraction_method: string
  document_count: number
  relevance_score: number
}

export interface RelationshipSearchResponse {
  results: RelationshipSearchResult[]
  total_count: number
  offset: number
  query: string
}

export interface SuggestResponse {
  entities: { text: string; type: string }[]
  documents: { id: string; title: string }[]
}

export interface FacetsResponse {
  source_feeds: { name: string; count: number }[]
  categories: { name: string; count: number }[]
  entity_types: { name: string; count: number }[]
  processing_statuses: { name: string; count: number }[]
  date_range: { earliest: string; latest: string }
}

// --- Selected Element ---

export type SelectedElement =
  | { type: 'entity'; id: string }
  | { type: 'cluster'; id: string }
  | { type: 'relationship'; id: string }
  | { type: 'anomaly'; id: string }
  | null
