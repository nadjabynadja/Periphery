// ============================================
// Periphery API Client
// Handles all backend communication with error handling,
// timeouts, retry logic, and WebSocket management
// ============================================

import type {
  HealthStatus,
  LegacyOntologySnapshot,
  OntologySnapshot,
  AnalyticalQueryResponse,
  EntityDetail,
  ClusterDetail,
  PipelineStats,
  EmbeddingStats,
  CriticMonitoring,
  CriticExplanation,
  CriticScoreTrend,
  QueryHistoryEntry,
  SnapshotFilters,
  SnapshotDelta,
  QueryUpdate,
  ConnectionStatus,
  DocumentSearchResponse,
  EntitySearchResponse,
  RelationshipSearchResponse,
  SuggestResponse,
  FacetsResponse,
} from './types'
import { PeripheryWebSocket, type WSMessage } from './websocket'

// --- Pipeline Command Types ---
export interface CommandResponse {
  status: string
  pid?: number
  command?: string
}

export interface CommandStatusEntry {
  state: 'running' | 'stopped'
  pid: number | null
}

export type CommandStatusMap = Record<string, CommandStatusEntry>

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

const DEFAULT_TIMEOUT = 10_000
const QUERY_TIMEOUT = 30_000

// --- HTTP Client ---

class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    message?: string,
  ) {
    super(message || `API error: ${status} ${statusText}`)
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  options?: RequestInit & { timeout?: number },
): Promise<T> {
  const { timeout = DEFAULT_TIMEOUT, ...init } = options || {}

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  try {
    const token = localStorage.getItem('periphery_session')
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    const res = await fetch(`${BASE_URL}${path}`, {
      headers,
      signal: controller.signal,
      ...init,
    })

    if (!res.ok) {
      throw new ApiError(res.status, res.statusText)
    }

    return res.json()
  } finally {
    clearTimeout(timer)
  }
}

async function requestWithRetry<T>(
  path: string,
  options?: RequestInit & { timeout?: number },
): Promise<T> {
  try {
    return await request<T>(path, options)
  } catch (err) {
    // Retry once on network failure, no retry on 4xx
    if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
      throw err
    }
    // One retry on network/timeout errors
    return request<T>(path, options)
  }
}

// --- Snapshot Cache ---

let cachedSnapshot: OntologySnapshot | null = null
let snapshotTimestamp = 0
let snapshotRefreshInterval = 30_000

export function setSnapshotRefreshInterval(ms: number) {
  snapshotRefreshInterval = ms
}

export function getCachedSnapshot(): OntologySnapshot | null {
  return cachedSnapshot
}

export function setCachedSnapshot(snapshot: OntologySnapshot) {
  cachedSnapshot = snapshot
  snapshotTimestamp = Date.now()
}

function isSnapshotStale(): boolean {
  return Date.now() - snapshotTimestamp > snapshotRefreshInterval
}

// --- WebSocket Manager ---

type WsCallback = (data: SnapshotDelta | QueryUpdate) => void

class WebSocketManager {
  private ws: WebSocket | null = null
  private reconnectAttempts = 0
  private maxReconnectDelay = 30_000
  private listeners = new Map<string, Set<WsCallback>>()
  private updateBuffer: (SnapshotDelta | QueryUpdate)[] = []
  private flushTimer: ReturnType<typeof setInterval> | null = null
  private _status: ConnectionStatus = 'disconnected'
  private statusListeners = new Set<(status: ConnectionStatus) => void>()

  get status(): ConnectionStatus {
    return this._status
  }

  private setStatus(status: ConnectionStatus) {
    this._status = status
    this.statusListeners.forEach(cb => cb(status))
  }

  onStatusChange(cb: (status: ConnectionStatus) => void): () => void {
    this.statusListeners.add(cb)
    return () => this.statusListeners.delete(cb)
  }

  connect(path: string = '/ws/snapshot') {
    if (this.ws?.readyState === WebSocket.OPEN) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    let wsBase: string
    if (BASE_URL) {
      // Convert http(s) BASE_URL to ws(s)
      wsBase = BASE_URL.replace(/^http/, 'ws')
    } else {
      wsBase = `${protocol}//${window.location.host}`
    }
    const url = `${wsBase}${path}`

    try {
      this.ws = new WebSocket(url)
      this.setStatus('reconnecting')

      this.ws.onopen = () => {
        this.reconnectAttempts = 0
        this.setStatus('connected')
        this.startBufferFlush()
      }

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          this.updateBuffer.push(data)
        } catch {
          // Ignore malformed messages
        }
      }

      this.ws.onclose = () => {
        this.setStatus('disconnected')
        this.stopBufferFlush()
        this.scheduleReconnect(path)
      }

      this.ws.onerror = () => {
        this.ws?.close()
      }
    } catch {
      this.setStatus('disconnected')
      this.scheduleReconnect(path)
    }
  }

  private scheduleReconnect(path: string) {
    const delay = Math.min(
      1000 * Math.pow(2, this.reconnectAttempts),
      this.maxReconnectDelay,
    )
    this.reconnectAttempts++
    this.setStatus('reconnecting')
    setTimeout(() => this.connect(path), delay)
  }

  private startBufferFlush() {
    this.flushTimer = setInterval(() => {
      if (this.updateBuffer.length === 0) return
      const batch = this.updateBuffer.splice(0)
      batch.forEach(msg => {
        const channel = msg.type || 'snapshot_delta'
        const listeners = this.listeners.get(channel)
        listeners?.forEach(cb => cb(msg))
      })
    }, 500) // Flush every 500ms to avoid rendering thrash
  }

  private stopBufferFlush() {
    if (this.flushTimer) {
      clearInterval(this.flushTimer)
      this.flushTimer = null
    }
  }

  subscribe(channel: string, callback: WsCallback): () => void {
    if (!this.listeners.has(channel)) {
      this.listeners.set(channel, new Set())
    }
    this.listeners.get(channel)!.add(callback)
    return () => {
      this.listeners.get(channel)?.delete(callback)
    }
  }

  disconnect() {
    this.stopBufferFlush()
    this.ws?.close()
    this.ws = null
    this.setStatus('disconnected')
  }
}

export const wsManager = new WebSocketManager()

// --- API Functions ---

export const peripheryApi = {
  // --- Health ---
  getHealth(): Promise<HealthStatus> {
    return requestWithRetry<HealthStatus>('/health')
  },

  // --- Ontology Snapshot (new analytical API) ---
  async getSnapshot(filters?: SnapshotFilters): Promise<OntologySnapshot> {
    if (!isSnapshotStale() && cachedSnapshot && !filters) {
      return cachedSnapshot
    }

    const params = new URLSearchParams()
    if (filters?.confidence_floor != null) {
      params.set('confidence_floor', String(filters.confidence_floor))
    }
    if (filters?.cluster_ids?.length) {
      filters.cluster_ids.forEach(id => params.append('cluster_ids', id))
    }
    if (filters?.include_rendering !== undefined) {
      params.set('include_rendering', String(filters.include_rendering))
    }

    const qs = params.toString()
    const path = `/api/snapshot${qs ? `?${qs}` : ''}`

    try {
      const snapshot = await requestWithRetry<OntologySnapshot>(path)
      if (!filters) {
        setCachedSnapshot(snapshot)
      }
      return snapshot
    } catch {
      // Fall back to legacy endpoint
      const legacy = await requestWithRetry<LegacyOntologySnapshot>('/crystallizer/graph')
      const converted = convertLegacySnapshot(legacy)
      if (!filters) {
        setCachedSnapshot(converted)
      }
      return converted
    }
  },

  // --- Entity Detail ---
  getEntity(canonicalId: string): Promise<EntityDetail> {
    return requestWithRetry<EntityDetail>(`/api/entity/${encodeURIComponent(canonicalId)}`)
  },

  // --- Cluster Detail ---
  getCluster(clusterId: string): Promise<ClusterDetail> {
    return requestWithRetry<ClusterDetail>(`/api/cluster/${encodeURIComponent(clusterId)}`)
  },

  // --- Query ---
  async query(
    text: string,
    options?: { top_k?: number; session_id?: string },
  ): Promise<AnalyticalQueryResponse> {
    const raw = await requestWithRetry<any>('/api/query', {
      method: 'POST',
      body: JSON.stringify({ query: text, ...options }),
      timeout: QUERY_TIMEOUT,
    })
    return adaptQueryResponse(raw)
  },

  // --- Query History ---
  async getQueryHistory(limit = 20, sessionId?: string): Promise<QueryHistoryEntry[]> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (sessionId) params.set('session_id', sessionId)
    const res = await requestWithRetry<{ queries: QueryHistoryEntry[]; stats: Record<string, unknown> }>(`/api/history?${params}`)
    return res.queries ?? []
  },

  // --- Pipeline Stats ---
  getPipelineStats(): Promise<PipelineStats> {
    return requestWithRetry<PipelineStats>('/pipeline/stats')
  },

  getEmbeddingStats(): Promise<EmbeddingStats> {
    return requestWithRetry<EmbeddingStats>('/pipeline/embedding-stats')
  },

  // --- Critic ---
  getCriticMonitoring(): Promise<CriticMonitoring> {
    return requestWithRetry<CriticMonitoring>('/critic/monitoring')
  },

  getCriticExplanations(): Promise<CriticExplanation[]> {
    return requestWithRetry<CriticExplanation[]>('/critic/explanations')
  },

  getCriticScoreTrend(): Promise<CriticScoreTrend> {
    return requestWithRetry<CriticScoreTrend>('/critic/score-trend')
  },

  // --- Crystallizer ---
  getAnomalies(): Promise<{ anomalies: Anomaly[] }> {
    return requestWithRetry('/crystallizer/snapshot/anomalies')
  },

  getTrajectories(): Promise<{ trajectories: Trajectory[] }> {
    return requestWithRetry('/crystallizer/snapshot/trajectories')
  },

  getEmergingStructures(): Promise<{ structures: EmergingStructure[] }> {
    return requestWithRetry('/crystallizer/snapshot/emerging')
  },

  triggerCrystallize(): Promise<Record<string, unknown>> {
    return requestWithRetry('/crystallizer/crystallize', { method: 'POST' })
  },

  getCrystallizerStats(): Promise<Record<string, unknown>> {
    return requestWithRetry('/crystallizer/stats')
  },

  // --- Legacy endpoints ---
  getLegacyGraph(): Promise<LegacyOntologySnapshot> {
    return requestWithRetry<LegacyOntologySnapshot>('/crystallizer/graph')
  },

  getSubgraph(nodeId: string, depth = 2): Promise<LegacyOntologySnapshot> {
    return requestWithRetry<LegacyOntologySnapshot>(
      `/crystallizer/graph/${encodeURIComponent(nodeId)}?depth=${depth}`,
    )
  },

  getLegacyClusters(): Promise<{ id: number; document_ids: string[]; label: string | null; coherence_score: number | null }[]> {
    return requestWithRetry('/crystallizer/clusters')
  },

  getCriticScores(): Promise<{ cluster_id: number; coherence_score: number; document_count: number }[]> {
    return requestWithRetry('/critic/scores')
  },

  // --- Ingest ---
  ingest(content: string, contentType = 'text/plain', metadata: Record<string, unknown> = {}) {
    return requestWithRetry<{ document_ids: string[]; count: number }>('/ingest/', {
      method: 'POST',
      body: JSON.stringify({ content, content_type: contentType, metadata }),
    })
  },

  getIngestStats() {
    return requestWithRetry<{ total_documents: number; total_vectors: number; embedding_dim: number }>('/ingest/stats')
  },

  // --- Legibility Gradient ---
  getLegibilityGradient(): Promise<Record<string, unknown>> {
    return requestWithRetry('/api/legibility-gradient')
  },

  // --- Feedback ---
  submitFeedback(queryId: string, feedback: Record<string, unknown>) {
    return requestWithRetry(`/api/feedback/${encodeURIComponent(queryId)}`, {
      method: 'POST',
      body: JSON.stringify(feedback),
    })
  },

  // --- Annotations ---
  submitAnnotation(annotation: Record<string, unknown>) {
    return requestWithRetry('/api/annotate', {
      method: 'POST',
      body: JSON.stringify(annotation),
    })
  },

  // --- Pipeline Commands ---
  forceIngest(): Promise<CommandResponse> {
    return request<CommandResponse>('/api/commands/force-ingest', { method: 'POST' })
  },

  runCollect(): Promise<CommandResponse> {
    return request<CommandResponse>('/api/commands/run-collect', { method: 'POST' })
  },

  continuousCollect(): Promise<CommandResponse> {
    return request<CommandResponse>('/api/commands/continuous-collect', { method: 'POST' })
  },

  getCommandStatus(): Promise<CommandStatusMap> {
    return requestWithRetry<CommandStatusMap>('/api/commands/status')
  },

  stopCommand(name: string): Promise<CommandResponse> {
    return request<CommandResponse>(`/api/commands/stop/${encodeURIComponent(name)}`, { method: 'POST' })
  },

  // --- Search ---
  searchDocuments(params: {
    q: string; source_feed?: string; category?: string;
    date_from?: string; date_to?: string; status?: string;
    limit?: number; offset?: number;
  }): Promise<DocumentSearchResponse> {
    const qs = new URLSearchParams()
    qs.set('q', params.q)
    if (params.source_feed) qs.set('source_feed', params.source_feed)
    if (params.category) qs.set('category', params.category)
    if (params.date_from) qs.set('date_from', params.date_from)
    if (params.date_to) qs.set('date_to', params.date_to)
    if (params.status) qs.set('status', params.status)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    return requestWithRetry<DocumentSearchResponse>(`/api/search/documents?${qs}`)
  },

  searchEntities(params: {
    q: string; entity_type?: string; has_location?: boolean;
    min_confidence?: number; limit?: number; offset?: number;
  }): Promise<EntitySearchResponse> {
    const qs = new URLSearchParams()
    qs.set('q', params.q)
    if (params.entity_type) qs.set('entity_type', params.entity_type)
    if (params.has_location != null) qs.set('has_location', String(params.has_location))
    if (params.min_confidence != null) qs.set('min_confidence', String(params.min_confidence))
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    return requestWithRetry<EntitySearchResponse>(`/api/search/entities?${qs}`)
  },

  searchRelationships(params: {
    q: string; predicate?: string; min_confidence?: number;
    limit?: number; offset?: number;
  }): Promise<RelationshipSearchResponse> {
    const qs = new URLSearchParams()
    qs.set('q', params.q)
    if (params.predicate) qs.set('predicate', params.predicate)
    if (params.min_confidence != null) qs.set('min_confidence', String(params.min_confidence))
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    return requestWithRetry<RelationshipSearchResponse>(`/api/search/relationships?${qs}`)
  },

  searchSuggest(params: { q: string; limit?: number }): Promise<SuggestResponse> {
    const qs = new URLSearchParams({ q: params.q })
    if (params.limit != null) qs.set('limit', String(params.limit))
    return requestWithRetry<SuggestResponse>(`/api/search/suggest?${qs}`)
  },

  searchFacets(params?: { q?: string }): Promise<FacetsResponse> {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    const qsStr = qs.toString()
    return requestWithRetry<FacetsResponse>(`/api/search/facets${qsStr ? `?${qsStr}` : ''}`)
  },

  // --- Auth ---
  startChallenge(): Promise<{ challenge_id: string; qr_data: string; expires_at: string }> {
    return request('/auth/challenge', { method: 'POST' })
  },

  pollChallengeStatus(challengeId: string): Promise<{ status: string; user_display_name?: string }> {
    return request(`/auth/challenge/${encodeURIComponent(challengeId)}/status`)
  },

  scanChallenge(challengeId: string, userId: string): Promise<{ challenge_code: string }> {
    return request(`/auth/challenge/${encodeURIComponent(challengeId)}/scan`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId }),
    })
  },

  confirmChallenge(challengeId: string, code: string): Promise<{
    session_token: string; user_id: string; org_id: string;
    display_name: string; role: string; expires_at: string;
  }> {
    return request(`/auth/challenge/${encodeURIComponent(challengeId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
  },

  logout(): Promise<{ ok: boolean }> {
    return request('/auth/logout', { method: 'POST' })
  },

  getMe(): Promise<{
    user_id: string; org_id: string; org_name: string;
    display_name: string; role: string;
  }> {
    return request('/auth/me')
  },

  createOrg(name: string): Promise<{ org_id: string; name: string }> {
    return request('/auth/orgs', { method: 'POST', body: JSON.stringify({ name }) })
  },

  listOrgs(): Promise<{ org_id: string; name: string; created_at: string }[]> {
    return request('/auth/orgs')
  },

  createUser(orgId: string, displayName: string, role = 'analyst'): Promise<{
    user_id: string; org_id: string; display_name: string; role: string;
  }> {
    return request(`/auth/orgs/${encodeURIComponent(orgId)}/users`, {
      method: 'POST',
      body: JSON.stringify({ display_name: displayName, role }),
    })
  },

  getPersonalOverlay(): Promise<any> {
    return request('/api/personal/overlay')
  },
}

// --- Helpers ---

import type { Anomaly, EmergingStructure, Trajectory } from './types'

/**
 * Adapt the backend AnalyticalQueryResponse (which uses synthesis/results/execution_stats)
 * into the flattened shape the frontend components expect.
 */
function adaptQueryResponse(raw: any): AnalyticalQueryResponse {
  const synthesis = raw.synthesis ?? {}
  const results = raw.results ?? {}
  const stats = raw.execution_stats ?? {}

  // Map synthesis key_findings (strings) to Finding objects
  const keyFindings = (synthesis.key_findings ?? []).map((text: string) => ({
    text,
    confidence: 0.5,
    supporting_entity_ids: [],
  }))

  // Map backend EntityResult to frontend EntityResult shape
  const entities = (results.entities ?? []).map((e: any) => ({
    canonical_id: e.canonical_id ?? '',
    name: e.name ?? '',
    entity_type: e.type ?? e.entity_type ?? '',
    confidence: e.confidence ?? 0,
    relevance_score: e.relevance_score ?? 0,
    source_count: e.source_documents?.length ?? e.source_count ?? 0,
    temporal_context: e.temporal_context?.temporal_focus ?? e.temporal_context ?? '',
  }))

  // Map backend RelationshipResult to frontend shape
  const relationships = (results.relationships ?? []).map((r: any) => ({
    subject_name: r.subject?.name ?? r.subject_name ?? '',
    predicate: r.predicate ?? '',
    object_name: r.object?.name ?? r.object_name ?? '',
    confidence: r.confidence ?? 0,
    relevance_score: r.relevance_score ?? 0,
    evidence_snippet: (r.evidence ?? [])[0] ?? r.evidence_snippet ?? '',
  }))

  // Map backend ClusterResult to frontend shape
  const clusters = (results.clusters ?? []).map((c: any) => ({
    cluster_id: c.cluster_id ?? '',
    label: c.label ?? '',
    confidence: c.confidence ?? 0,
    relevance_score: c.relevance_score ?? 0,
    member_count: c.size ?? c.member_count ?? 0,
  }))

  // Map backend TrajectoryResult
  const trajectories = (results.trajectories ?? []).map((t: any) => ({
    trajectory_id: t.trajectory_id ?? '',
    cluster_label: t.cluster_label ?? '',
    pattern: t.pattern ?? '',
    velocity: t.velocity ?? 0,
  }))

  // Map backend AnomalyResult
  const anomalies = (results.anomalies ?? []).map((a: any) => ({
    anomaly_id: a.anomaly_id ?? '',
    anomaly_type: a.type ?? a.anomaly_type ?? '',
    anomaly_score: a.score ?? a.anomaly_score ?? 0,
    description: a.description ?? '',
  }))

  const parsedIntent = {
    ...raw.parsed_intent,
    intent_type: raw.parsed_intent?.query_type ?? raw.parsed_intent?.intent_type ?? 'unknown',
    entity_mentions: raw.parsed_intent?.entities_referenced ?? raw.parsed_intent?.entity_mentions ?? [],
  }

  return {
    query_id: raw.query_id,
    parsed_intent: parsedIntent,
    synthesis,
    results,
    execution_stats: stats,
    // Flattened convenience fields
    narrative: synthesis.summary ?? '',
    key_findings: keyFindings,
    entities,
    relationships,
    clusters,
    trajectories,
    anomalies,
    gaps: synthesis.gaps_and_limitations ?? [],
    suggested_followups: synthesis.suggested_followups ?? [],
    confidence: entities.length > 0
      ? entities.reduce((sum: number, e: any) => sum + e.confidence, 0) / entities.length
      : 0,
    processing_time_ms: stats.total_time_ms ?? 0,
  }
}

function convertLegacySnapshot(legacy: LegacyOntologySnapshot): OntologySnapshot {
  return {
    entities: legacy.nodes.map(n => ({
      canonical_id: n.id,
      name: n.label,
      entity_type: n.node_type,
      aliases: [],
      confidence: n.coherence_score ?? 0.5,
      source_count: 1,
      cluster_ids: n.cluster_id != null ? [String(n.cluster_id)] : [],
      first_seen: new Date().toISOString(),
      last_seen: new Date().toISOString(),
      rendering: computeRendering(n.coherence_score ?? 0.5),
    })),
    relationships: legacy.edges.map((e, i) => ({
      id: `rel-${i}`,
      subject_id: e.source,
      predicate: 'related_to',
      object_id: e.target,
      confidence: e.weight,
      evidence_sentences: [],
      temporal_context: 'current',
      extraction_tier: 'co_occurrence',
      source_count: 1,
      first_seen: new Date().toISOString(),
      last_seen: new Date().toISOString(),
    })),
    clusters: [],
    trajectories: [],
    anomalies: [],
    gradients: [],
    emerging_structures: [],
    timestamp: new Date().toISOString(),
    entity_count: legacy.document_count,
    relationship_count: legacy.edges.length,
    cluster_count: legacy.cluster_count,
  }
}

function computeRendering(confidence: number): import('./types').RenderingMetadata {
  if (confidence >= 0.8) {
    return {
      opacity: 1.0, blur: 0, glow_intensity: 0.8, glow_color: '#00D4FF',
      border_style: 'solid', label_visibility: 'full', pulse_animation: false,
      pulse_speed: 0, size_multiplier: 1.2, tier: 'solid',
    }
  } else if (confidence >= 0.6) {
    return {
      opacity: 0.85, blur: 0, glow_intensity: 0.5, glow_color: '#00D4FF',
      border_style: 'solid', label_visibility: 'full', pulse_animation: false,
      pulse_speed: 0, size_multiplier: 1.0, tier: 'defined',
    }
  } else if (confidence >= 0.4) {
    return {
      opacity: 0.6, blur: 1, glow_intensity: 0.3, glow_color: '#FFB833',
      border_style: 'dashed', label_visibility: 'on_hover', pulse_animation: true,
      pulse_speed: 3000, size_multiplier: 0.9, tier: 'emerging',
    }
  } else if (confidence >= 0.2) {
    return {
      opacity: 0.35, blur: 3, glow_intensity: 0.15, glow_color: '#3A4A5C',
      border_style: 'none', label_visibility: 'on_hover', pulse_animation: true,
      pulse_speed: 5000, size_multiplier: 0.7, tier: 'haze',
    }
  } else {
    return {
      opacity: 0.15, blur: 5, glow_intensity: 0.05, glow_color: '#2A3040',
      border_style: 'none', label_visibility: 'on_click_only', pulse_animation: true,
      pulse_speed: 8000, size_multiplier: 0.5, tier: 'whisper',
    }
  }
}

export { computeRendering }

// --- WebSocket subscription helpers ---

const WS_BASE = BASE_URL ? BASE_URL.replace(/^http/, 'ws') : ''

// wsManager (WebSocketManager) is the active WebSocket implementation used by App.tsx.
// The unused PeripheryWebSocket-based helpers (snapshotPWS, onSnapshotUpdate,
// onNewDocument, getConnectionStatus, onConnectionStatusChange) have been removed
// to avoid opening a duplicate /ws/snapshot connection on startup. (M14)

export function subscribeToQuery(queryId: string, handler: (data: any) => void): () => void {
  const wsBase = WS_BASE || `${typeof window !== 'undefined' ? (window.location.protocol === 'https:' ? 'wss:' : 'ws:') : 'ws:'}//` + (typeof window !== 'undefined' ? window.location.host : 'localhost:8000')
  const queryWS = new PeripheryWebSocket(`${wsBase}/ws/query/${queryId}`)
  queryWS.connect()
  const unsub = queryWS.on('query_update', (msg: WSMessage) => {
    if (msg.data) handler(msg.data)
  })
  // Return cleanup function that unsubscribes AND disconnects
  return () => {
    unsub()
    queryWS.disconnect()
  }
}
