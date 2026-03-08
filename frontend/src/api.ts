const BASE = ''

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

export interface GraphNode {
  id: string
  label: string
  cluster_id: number | null
  coherence_score: number | null
  node_type: string
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
}

export interface OntologySnapshot {
  nodes: GraphNode[]
  edges: GraphEdge[]
  cluster_count: number
  document_count: number
}

export interface Cluster {
  id: number
  document_ids: string[]
  label: string | null
  coherence_score: number | null
}

export interface CriticScore {
  cluster_id: number
  coherence_score: number
  document_count: number
}

export interface QueryResponse {
  answer: string
  sources: SearchResult[]
  confidence: number
  graph_context: OntologySnapshot | null
}

export interface IngestResponse {
  document_ids: string[]
  count: number
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export const api = {
  // Ingest
  ingest(content: string, contentType = 'text/plain', metadata: Record<string, unknown> = {}) {
    return request<IngestResponse>('/ingest/', {
      method: 'POST',
      body: JSON.stringify({ content, content_type: contentType, metadata }),
    })
  },

  ingestBatch(documents: { content: string; content_type?: string; metadata?: Record<string, unknown> }[]) {
    return request<IngestResponse>('/ingest/batch', {
      method: 'POST',
      body: JSON.stringify({ documents }),
    })
  },

  search(query: string, topK = 10) {
    return request<SearchResult[]>('/ingest/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    })
  },

  getIngestStats() {
    return request<{ total_documents: number; total_vectors: number; embedding_dim: number }>('/ingest/stats')
  },

  // Crystallizer
  getClusters() {
    return request<Cluster[]>('/crystallizer/clusters')
  },

  getGraph() {
    return request<OntologySnapshot>('/crystallizer/graph')
  },

  getSubgraph(nodeId: string, depth = 2) {
    return request<OntologySnapshot>(`/crystallizer/graph/${nodeId}?depth=${depth}`)
  },

  triggerCrystallize() {
    return request<Record<string, unknown>>('/crystallizer/crystallize', { method: 'POST' })
  },

  getCrystallizerStats() {
    return request<Record<string, unknown>>('/crystallizer/stats')
  },

  getBridges() {
    return request<{ bridge_documents: string[] }>('/crystallizer/bridges')
  },

  // Critic
  getCriticScores() {
    return request<CriticScore[]>('/critic/scores')
  },

  evaluateDocument(documentId: string) {
    return request<Record<string, unknown>>(`/critic/evaluate?document_id=${documentId}`, {
      method: 'POST',
    })
  },

  getOutliers(limit = 10) {
    return request<{ outliers: { document_id: string; cluster_id: number; coherence_score: number }[] }>(
      `/critic/outliers?limit=${limit}`
    )
  },

  trainCritic(epochs = 10) {
    return request<Record<string, unknown>>(`/critic/train?epochs=${epochs}`, { method: 'POST' })
  },

  // Query
  query(question: string, topK = 10) {
    return request<QueryResponse>('/query/', {
      method: 'POST',
      body: JSON.stringify({ question, top_k: topK }),
    })
  },

  findSimilar(text: string, topK = 10) {
    return request<SearchResult[]>('/query/similar', {
      method: 'POST',
      body: JSON.stringify({ query: text, top_k: topK }),
    })
  },

  // Health
  getHealth() {
    return request<{ status: string; vectors: number; clusters: number; last_crystallization: string | null }>(
      '/health'
    )
  },
}
