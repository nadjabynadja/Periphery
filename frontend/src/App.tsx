// ============================================
// App — Main layout with auth, panels, WebSocket
// ============================================

import React, { useEffect, useCallback } from 'react'
import { useStore } from './store'
import { peripheryApi, wsManager } from './api/client'
import type { SnapshotDelta } from './api/types'

import { AuthProvider } from './components/auth/AuthProvider'
import { SystemStatusBar } from './components/SystemStatusBar'
import { DataFeedSidebar } from './components/DataFeedSidebar'
import { SearchPanel } from './components/search/SearchPanel'
import { OntologyGraph } from './components/graph/OntologyGraph'
import { GeographicOverlay } from './components/graph/GeographicOverlay'
import { TemporalTimeline } from './components/graph/TemporalTimeline'
import { QueryBar } from './components/query/QueryBar'
import { QueryResults } from './components/query/QueryResults'
import { DetailPanel } from './components/detail/DetailPanel'

const AppContent: React.FC = () => {
  const viewMode = useStore((s) => s.viewMode)
  const setSnapshot = useStore((s) => s.setSnapshot)
  const setEntities = useStore((s) => s.setEntities)
  const setRelationships = useStore((s) => s.setRelationships)
  const setLoadingEntities = useStore((s) => s.setLoadingEntities)
  const setConnectionStatus = useStore((s) => s.setConnectionStatus)
  const setQueryHistory = useStore((s) => s.setQueryHistory)
  const confidenceFloor = useStore((s) => s.confidenceFloor)

  // Load initial data
  const loadData = useCallback(async () => {
    setLoadingEntities(true)
    try {
      const [snapshot, entitiesRes, relsRes] = await Promise.allSettled([
        peripheryApi.getSnapshot({ confidence_floor: confidenceFloor, include_rendering: true }),
        peripheryApi.getEntities({ limit: 500 }),
        peripheryApi.getRelationships({ limit: 1000 }),
      ])

      if (snapshot.status === 'fulfilled') setSnapshot(snapshot.value)
      if (entitiesRes.status === 'fulfilled') setEntities(entitiesRes.value.entities)
      if (relsRes.status === 'fulfilled') setRelationships(relsRes.value.relationships)
    } catch {
      // Data load failed — UI shows empty state
    } finally {
      setLoadingEntities(false)
    }
  }, [setSnapshot, setEntities, setRelationships, setLoadingEntities, confidenceFloor])

  // Load query history
  useEffect(() => {
    peripheryApi.getQueryHistory(20).then(setQueryHistory).catch(() => {})
  }, [setQueryHistory])

  // Initial data load + periodic refresh
  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 30_000)
    return () => clearInterval(interval)
  }, [loadData])

  // WebSocket connection
  useEffect(() => {
    wsManager.connect('/ws/snapshot')

    const unsubStatus = wsManager.onStatusChange(setConnectionStatus)
    const unsubDelta = wsManager.subscribe('snapshot_delta', (data) => {
      const delta = data as SnapshotDelta
      // Apply delta to entities
      const store = useStore.getState()
      const currentEntities = [...store.entities]
      const entityMap = new Map(currentEntities.map((e) => [e.canonical_id, e]))

      // Remove
      for (const id of delta.removed_entity_ids) {
        entityMap.delete(id)
      }

      // Update
      for (const entity of delta.updated_entities) {
        entityMap.set(entity.canonical_id, entity)
      }

      // Add
      for (const entity of delta.added_entities) {
        entityMap.set(entity.canonical_id, entity)
      }

      setEntities(Array.from(entityMap.values()))

      // Apply relationship delta
      if (delta.added_relationships.length > 0) {
        const currentRels = [...store.relationships]
        const removedIds = new Set(delta.removed_relationship_ids)
        const filtered = currentRels.filter((r) => !removedIds.has(r.id))
        setRelationships([...filtered, ...delta.added_relationships])
      }
    })

    return () => {
      unsubStatus()
      unsubDelta()
      wsManager.disconnect()
    }
  }, [setConnectionStatus, setEntities, setRelationships])

  // Render the active visualization
  const renderVisualization = () => {
    switch (viewMode) {
      case 'graph':
        return <OntologyGraph />
      case 'map':
        return <GeographicOverlay />
      case 'timeline':
        return <TemporalTimeline />
      default:
        return <OntologyGraph />
    }
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* Scanline effect */}
      <div className="scanline-overlay" />

      {/* Status bar */}
      <SystemStatusBar />

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar — data feed */}
        <DataFeedSidebar />

        {/* Center — visualization + query */}
        <div className="flex flex-col flex-1 overflow-hidden">
          {/* Visualization area */}
          <div className="flex-1 overflow-hidden relative">
            {renderVisualization()}
          </div>

          {/* Query results */}
          <QueryResults />

          {/* Query bar */}
          <QueryBar />
        </div>

        {/* Right — detail panel */}
        <DetailPanel />
      </div>

      {/* Search overlay */}
      <SearchPanel />
    </div>
  )
}

const App: React.FC = () => {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}

export default App
