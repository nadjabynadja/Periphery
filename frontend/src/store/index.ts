// ============================================
// Periphery Global State (Zustand)
// ============================================

import { create } from 'zustand'
import type {
  OntologySnapshot,
  SelectedElement,
  ViewMode,
  ConnectionStatus,
  AnalyticalQueryResponse,
  QueryHistoryEntry,
  PipelineStats,
  CriticMonitoring,
  HealthStatus,
} from '../api/types'

interface PeripheryState {
  // Ontology snapshot (cached)
  snapshot: OntologySnapshot | null
  setSnapshot: (s: OntologySnapshot) => void

  // Selected element in graph
  selectedElement: SelectedElement
  setSelectedElement: (el: SelectedElement) => void

  // View mode
  viewMode: ViewMode
  setViewMode: (mode: ViewMode) => void

  // Panel states
  detailPanelWidth: number
  setDetailPanelWidth: (w: number) => void
  feedSidebarWidth: number
  setFeedSidebarWidth: (w: number) => void
  queryPanelHeight: number
  setQueryPanelHeight: (h: number) => void
  queryPanelExpanded: boolean
  setQueryPanelExpanded: (expanded: boolean) => void

  // Connection
  connectionStatus: ConnectionStatus
  setConnectionStatus: (s: ConnectionStatus) => void

  // Query
  currentQuery: string
  setCurrentQuery: (q: string) => void
  queryResult: AnalyticalQueryResponse | null
  setQueryResult: (r: AnalyticalQueryResponse | null) => void
  isQuerying: boolean
  setIsQuerying: (loading: boolean) => void
  queryHistory: QueryHistoryEntry[]
  addQueryToHistory: (entry: QueryHistoryEntry) => void
  setQueryHistory: (h: QueryHistoryEntry[]) => void

  // Pipeline & health
  pipelineStats: PipelineStats | null
  setPipelineStats: (s: PipelineStats | null) => void
  criticMonitoring: CriticMonitoring | null
  setCriticMonitoring: (m: CriticMonitoring | null) => void
  health: HealthStatus | null
  setHealth: (h: HealthStatus | null) => void

  // Graph settings
  graphSettings: GraphSettings
  setGraphSettings: (s: Partial<GraphSettings>) => void
  showGraphSettings: boolean
  setShowGraphSettings: (show: boolean) => void

  // Search highlight
  highlightedEntityIds: Set<string>
  setHighlightedEntityIds: (ids: Set<string>) => void
  clearHighlights: () => void

  // Confidence filter
  confidenceFloor: number
  setConfidenceFloor: (v: number) => void

  // Search panel
  searchPanelOpen: boolean
  setSearchPanelOpen: (open: boolean) => void
  searchQuery: string
  setSearchQuery: (q: string) => void
}

export interface GraphSettings {
  linkStrength: number
  chargeStrength: number
  centerStrength: number
  collideRadius: number
  clusterForce: number
  showLabels: boolean
  showEdgeLabels: boolean
  showClusterHulls: boolean
  showEmergingStructures: boolean
  animationSpeed: number
}

const defaultGraphSettings: GraphSettings = {
  linkStrength: 0.5,
  chargeStrength: -15,
  centerStrength: 0.5,
  collideRadius: 2,
  clusterForce: 0.3,
  showLabels: true,
  showEdgeLabels: false,
  showClusterHulls: true,
  showEmergingStructures: true,
  animationSpeed: 1.0,
}

export const useStore = create<PeripheryState>((set) => ({
  snapshot: null,
  setSnapshot: (s) => set({ snapshot: s }),

  selectedElement: null,
  setSelectedElement: (el) => set({ selectedElement: el }),

  viewMode: 'graph',
  setViewMode: (mode) => set({ viewMode: mode }),

  detailPanelWidth: 0,
  setDetailPanelWidth: (w) => set({ detailPanelWidth: w }),
  feedSidebarWidth: 260,
  setFeedSidebarWidth: (w) => set({ feedSidebarWidth: w }),
  queryPanelHeight: 0,
  setQueryPanelHeight: (h) => set({ queryPanelHeight: h }),
  queryPanelExpanded: false,
  setQueryPanelExpanded: (expanded) => set({ queryPanelExpanded: expanded }),

  connectionStatus: 'disconnected',
  setConnectionStatus: (s) => set({ connectionStatus: s }),

  currentQuery: '',
  setCurrentQuery: (q) => set({ currentQuery: q }),
  queryResult: null,
  setQueryResult: (r) => set({ queryResult: r }),
  isQuerying: false,
  setIsQuerying: (loading) => set({ isQuerying: loading }),
  queryHistory: [],
  addQueryToHistory: (entry) =>
    set((state) => ({ queryHistory: [entry, ...state.queryHistory].slice(0, 100) })),
  setQueryHistory: (h) => set({ queryHistory: h }),

  pipelineStats: null,
  setPipelineStats: (s) => set({ pipelineStats: s }),
  criticMonitoring: null,
  setCriticMonitoring: (m) => set({ criticMonitoring: m }),
  health: null,
  setHealth: (h) => set({ health: h }),

  graphSettings: defaultGraphSettings,
  setGraphSettings: (s) =>
    set((state) => ({ graphSettings: { ...state.graphSettings, ...s } })),
  showGraphSettings: false,
  setShowGraphSettings: (show) => set({ showGraphSettings: show }),

  highlightedEntityIds: new Set(),
  setHighlightedEntityIds: (ids) => set({ highlightedEntityIds: ids }),
  clearHighlights: () => set({ highlightedEntityIds: new Set() }),

  confidenceFloor: 0,
  setConfidenceFloor: (v) => set({ confidenceFloor: v }),

  searchPanelOpen: false,
  setSearchPanelOpen: (open) => set({ searchPanelOpen: open }),
  searchQuery: '',
  setSearchQuery: (q) => set({ searchQuery: q }),
}))
