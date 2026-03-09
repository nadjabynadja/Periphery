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
} from './types'

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
    const res = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
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
  query(
    text: string,
    options?: { top_k?: number; session_id?: string },
  ): Promise<AnalyticalQueryResponse> {
    return requestWithRetry<AnalyticalQueryResponse>('/api/query', {
      method: 'POST',
      body: JSON.stringify({ question: text, ...options }),
      timeout: QUERY_TIMEOUT,
    })
  },

  // --- Query History ---
  getQueryHistory(limit = 20, sessionId?: string): Promise<QueryHistoryEntry[]> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (sessionId) params.set('session_id', sessionId)
    return requestWithRetry<QueryHistoryEntry[]>(`/api/history?${params}`)
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
}

// --- Helpers ---

import type { Anomaly, EmergingStructure, Trajectory } from './types'

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
