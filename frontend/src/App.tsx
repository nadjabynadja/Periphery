// ============================================
// PERIPHERY — Intelligence Console
// Main Application Layout
// ============================================

import { useEffect, useCallback, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { peripheryApi, wsManager } from './api'
import { useStore } from './store'
import type { ViewMode } from './api/types'

import { SystemStatusBar } from './components/SystemStatusBar'
import { DataFeedSidebar } from './components/DataFeedSidebar'
import { SearchPanel } from './components/search/SearchPanel'
import { OntologyGraph } from './components/graph/OntologyGraph'
import { GeographicOverlay } from './components/graph/GeographicOverlay'
import { TemporalTimeline } from './components/graph/TemporalTimeline'
import { QueryBar } from './components/query/QueryBar'
import { QueryResults } from './components/query/QueryResults'
import { DetailPanel } from './components/detail/DetailPanel'
import { AuthProvider } from './components/auth/AuthProvider'
import { LoginPage } from './components/auth/LoginPage'

const VIEW_MODES: { id: ViewMode; label: string }[] = [
  { id: 'graph', label: 'GRAPH' },
  { id: 'map', label: 'MAP' },
  { id: 'timeline', label: 'TIMELINE' },
]

const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === 'true'

export default function App() {
  const setSnapshot = useStore(s => s.setSnapshot)
  const setEntities = useStore(s => s.setEntities)
  const setRelationships = useStore(s => s.setRelationships)
  const setLoadingEntities = useStore(s => s.setLoadingEntities)
  const setHealth = useStore(s => s.setHealth)
  const setPipelineStats = useStore(s => s.setPipelineStats)
  const setCriticMonitoring = useStore(s => s.setCriticMonitoring)
  const setConnectionStatus = useStore(s => s.setConnectionStatus)
  const viewMode = useStore(s => s.viewMode)
  const setViewMode = useStore(s => s.setViewMode)
  const selectedElement = useStore(s => s.selectedElement)
  const queryPanelExpanded = useStore(s => s.queryPanelExpanded)
  const feedSidebarWidth = useStore(s => s.feedSidebarWidth)
  const setFeedSidebarWidth = useStore(s => s.setFeedSidebarWidth)
  const snapshot = useStore(s => s.snapshot)
  const searchPanelOpen = useStore(s => s.searchPanelOpen)
  const setSearchPanelOpen = useStore(s => s.setSearchPanelOpen)

  const [isResizingSidebar, setIsResizingSidebar] = useState(false)
  const sidebarDragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  // --- Data fetching ---
  useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const data = await peripheryApi.getHealth()
      setHealth(data)
      return data
    },
    refetchInterval: 5000,
  })

  useQuery({
    queryKey: ['snapshot'],
    queryFn: async () => {
      const data = await peripheryApi.getSnapshot({ include_rendering: true })
      setSnapshot(data)
      // After snapshot loads, kick off entity fetch
      setLoadingEntities(true)
      peripheryApi.getEntities({ limit: 500 }).then(result => {
        setEntities(result.entities)
        setLoadingEntities(false)
      }).catch(() => {
        setLoadingEntities(false)
      })
      return data
    },
    refetchInterval: 30000,
  })

  useQuery({
    queryKey: ['pipelineStats'],
    queryFn: async () => {
      try {
        const data = await peripheryApi.getPipelineStats()
        setPipelineStats(data)
        return data
      } catch {
        return null
      }
    },
    refetchInterval: 10000,
  })

  useQuery({
    queryKey: ['criticMonitoring'],
    queryFn: async () => {
      try {
        const data = await peripheryApi.getCriticMonitoring()
        setCriticMonitoring(data)
        return data
      } catch {
        return null
      }
    },
    refetchInterval: 30000,
  })

  // --- WebSocket ---
  useEffect(() => {
    const unsubStatus = wsManager.onStatusChange(setConnectionStatus)
    try { wsManager.connect('/ws/snapshot') } catch { /* fallback to polling */ }

    const unsubDelta = wsManager.subscribe('snapshot_delta', () => {
      peripheryApi.getSnapshot({ include_rendering: true }).then(snap => {
        setSnapshot(snap)
        // Refresh entities when snapshot updates
        peripheryApi.getEntities({ limit: 500 }).then(result => {
          setEntities(result.entities)
        }).catch(() => {})
      }).catch(() => {})
    })

    return () => {
      unsubStatus()
      unsubDelta()
      wsManager.disconnect()
    }
  }, [setConnectionStatus, setSnapshot])

  // --- Ctrl+K to toggle search panel ---
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        const current = useStore.getState().searchPanelOpen
        setSearchPanelOpen(!current)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setSearchPanelOpen])

  // --- Sidebar resize ---
  const handleSidebarResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    sidebarDragRef.current = { startX: e.clientX, startWidth: feedSidebarWidth }
    setIsResizingSidebar(true)
  }, [feedSidebarWidth])

  useEffect(() => {
    if (!isResizingSidebar) return
    const handleMove = (e: MouseEvent) => {
      if (!sidebarDragRef.current) return
      const delta = e.clientX - sidebarDragRef.current.startX
      setFeedSidebarWidth(Math.max(180, Math.min(400, sidebarDragRef.current.startWidth + delta)))
    }
    const handleUp = () => {
      setIsResizingSidebar(false)
      sidebarDragRef.current = null
    }
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
  }, [isResizingSidebar, setFeedSidebarWidth])

  const detailPanelWidth = selectedElement ? 360 : 0
  const isAuthenticated = useStore(s => s.isAuthenticated)

  return (
    <AuthProvider>
    {AUTH_ENABLED && !isAuthenticated ? (
      <LoginPage />
    ) : (
    <div className="h-screen w-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
      <div className="scanline-overlay" />

      {/* System Status Bar — top, persistent */}
      <SystemStatusBar />

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden dashboard-layout" style={{ minHeight: 0 }}>
        {/* Left: Data Feed Sidebar / Search Panel */}
        <div className="shrink-0 overflow-hidden relative" style={{ width: feedSidebarWidth }}>
          {searchPanelOpen ? <SearchPanel /> : <DataFeedSidebar />}
          <div
            className="resize-handle vertical"
            style={{ right: 0 }}
            onMouseDown={handleSidebarResizeStart}
          />
        </div>

        {/* Center: Graph + Query */}
        <div className="flex-1 flex flex-col overflow-hidden" style={{ minWidth: 0 }}>
          {/* View mode selector */}
          <div className="flex items-center justify-between px-2 py-1 border-b border-surface-border bg-base-800 shrink-0">
            <div className="flex items-center gap-1">
              {VIEW_MODES.map(mode => (
                <button
                  key={mode.id}
                  onClick={() => setViewMode(mode.id)}
                  className={`px-2 py-0.5 text-xxs font-display font-semibold tracking-wider uppercase border transition-all ${
                    viewMode === mode.id
                      ? 'text-accent-cyan border-accent-cyan/30 bg-accent-cyan/5'
                      : 'text-text-dim border-transparent hover:text-text-secondary'
                  }`}
                  style={{ borderRadius: '2px' }}
                >
                  {mode.label}
                </button>
              ))}
            </div>
            <span className="data-readout">
              {snapshot
                ? `${(snapshot.total_entities ?? snapshot.entity_count).toLocaleString()} entities · ${(snapshot.total_relationships ?? snapshot.relationship_count).toLocaleString()} rels · ${snapshot.cluster_count} clusters`
                : 'Awaiting data...'}
            </span>
          </div>

          {/* Graph/Map/Timeline */}
          <div className="flex-1 relative" style={{ minHeight: 0 }}>
            {viewMode === 'graph' && <OntologyGraph />}
            {viewMode === 'map' && <GeographicOverlay />}
            {viewMode === 'timeline' && <TemporalTimeline />}
          </div>

          {/* Query Bar */}
          <div className="shrink-0 border-t border-surface-border">
            <QueryBar />
          </div>

          {/* Query Results Panel */}
          {queryPanelExpanded && <QueryResults />}
        </div>

        {/* Right: Detail Panel */}
        {selectedElement && (
          <div
            className="shrink-0 overflow-hidden border-l border-surface-border"
            style={{ width: detailPanelWidth }}
          >
            <DetailPanel />
          </div>
        )}
      </div>
    </div>
    )}
    </AuthProvider>
  )
}
