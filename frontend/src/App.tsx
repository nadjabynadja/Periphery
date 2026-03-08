import { useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence } from 'framer-motion'
import { api } from './api'
import { OntologyGraph } from './components/OntologyGraph'
import { QueryInterface } from './components/QueryInterface'
import { SystemStatusBar } from './components/SystemStatusBar'
import { DataFeed } from './components/DataFeed'
import { ConfidenceDistribution } from './components/ConfidenceDistribution'
import { EntityDetail } from './components/EntityDetail'
import { IngestPanel } from './components/IngestPanel'
import { CriticDashboard } from './components/CriticDashboard'
import type { GraphNode, QueryResponse } from './api'

export default function App() {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null)
  const [isQuerying, setIsQuerying] = useState(false)
  const [rightPanel, setRightPanel] = useState<'feed' | 'ingest' | 'critic'>('feed')

  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 5000,
  })

  const graph = useQuery({
    queryKey: ['graph'],
    queryFn: api.getGraph,
    refetchInterval: 15000,
  })

  const clusters = useQuery({
    queryKey: ['clusters'],
    queryFn: api.getClusters,
    refetchInterval: 30000,
  })

  const criticScores = useQuery({
    queryKey: ['criticScores'],
    queryFn: api.getCriticScores,
    refetchInterval: 30000,
  })

  const handleQuery = useCallback(async (question: string) => {
    setIsQuerying(true)
    try {
      const result = await api.query(question)
      setQueryResult(result)
    } finally {
      setIsQuerying(false)
    }
  }, [])

  const handleNodeSelect = useCallback((node: GraphNode | null) => {
    setSelectedNode(node)
  }, [])

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-base-900">
      {/* Scanline overlay */}
      <div className="scanline-overlay" />

      {/* Top bar: Title + Query Interface */}
      <header className="flex items-center gap-4 px-3 py-1.5 border-b border-surface-border bg-base-800 shrink-0">
        <div className="flex items-center gap-3 shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-accent-cyan" style={{ boxShadow: '0 0 8px #00d4ff88' }} />
            <h1 className="font-display text-sm font-bold tracking-[0.2em] text-text-bright uppercase m-0">
              Periphery
            </h1>
          </div>
          <span className="text-xxs text-text-dim font-mono tracking-wider">v0.2.0</span>
        </div>

        <div className="h-4 w-px bg-surface-border mx-1" />

        <div className="flex-1 max-w-2xl">
          <QueryInterface
            onSubmit={handleQuery}
            isLoading={isQuerying}
            result={queryResult}
          />
        </div>

        <div className="h-4 w-px bg-surface-border mx-1" />

        {/* Right panel switcher */}
        <div className="flex gap-1 shrink-0">
          {([
            { id: 'feed' as const, label: 'FEED' },
            { id: 'ingest' as const, label: 'INGEST' },
            { id: 'critic' as const, label: 'CRITIC' },
          ]).map(tab => (
            <button
              key={tab.id}
              onClick={() => setRightPanel(tab.id)}
              className={`px-2 py-1 text-xxs font-display font-semibold tracking-wider uppercase border border-transparent transition-all duration-150 ${
                rightPanel === tab.id
                  ? 'text-accent-cyan border-accent-cyan/30 bg-accent-cyan/5'
                  : 'text-text-dim hover:text-text-secondary'
              }`}
              style={{ borderRadius: '2px' }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </header>

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Ontology Graph (hero) + Confidence Distribution */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Ontology Graph — centerpiece */}
          <div className="flex-1 panel m-1.5 mb-0 flex flex-col" style={{ minHeight: 0 }}>
            <div className="panel-header">
              <div className="panel-title">
                <div className="panel-indicator" />
                <span>Crystallized Ontology</span>
              </div>
              <div className="flex items-center gap-3">
                {graph.data && (
                  <span className="data-readout">
                    {graph.data.cluster_count} clusters / {graph.data.document_count} docs / {graph.data.edges.length} edges
                  </span>
                )}
                <button
                  className="btn-secondary"
                  onClick={() => {
                    api.triggerCrystallize().then(() => {
                      graph.refetch()
                      clusters.refetch()
                    })
                  }}
                >
                  Re-crystallize
                </button>
              </div>
            </div>
            <div className="relative flex-1" style={{ minHeight: 0 }}>
              <OntologyGraph
                data={graph.data || null}
                criticScores={criticScores.data || null}
                onNodeSelect={handleNodeSelect}
                selectedNodeId={selectedNode?.id || null}
              />
            </div>
          </div>

          {/* Bottom: Confidence Distribution */}
          <div className="panel m-1.5" style={{ height: '140px', flexShrink: 0 }}>
            <div className="panel-header">
              <div className="panel-title">
                <div className="panel-indicator" style={{ backgroundColor: '#d4a000', boxShadow: '0 0 6px #d4a00066' }} />
                <span>Confidence Distribution</span>
              </div>
            </div>
            <div className="panel-body h-full">
              <ConfidenceDistribution
                clusters={clusters.data || []}
                criticScores={criticScores.data || []}
              />
            </div>
          </div>
        </div>

        {/* Right sidebar */}
        <div className="flex flex-col overflow-hidden" style={{ width: '340px', flexShrink: 0 }}>
          {/* Entity Detail (when node selected) */}
          <AnimatePresence>
            {selectedNode && (
              <EntityDetail
                node={selectedNode}
                graphData={graph.data || null}
                onClose={() => setSelectedNode(null)}
              />
            )}
          </AnimatePresence>

          {/* Right panel content */}
          <div className="flex-1 panel m-1.5 overflow-hidden flex flex-col" style={{ minHeight: 0 }}>
            {rightPanel === 'feed' && (
              <>
                <div className="panel-header">
                  <div className="panel-title">
                    <div className="panel-indicator" />
                    <span>Data Feed</span>
                  </div>
                </div>
                <div className="panel-body flex-1 overflow-y-auto">
                  <DataFeed />
                </div>
              </>
            )}

            {rightPanel === 'ingest' && (
              <>
                <div className="panel-header">
                  <div className="panel-title">
                    <div className="panel-indicator" />
                    <span>Data Ingestion</span>
                  </div>
                </div>
                <div className="panel-body flex-1 overflow-y-auto">
                  <IngestPanel />
                </div>
              </>
            )}

            {rightPanel === 'critic' && (
              <>
                <div className="panel-header">
                  <div className="panel-title">
                    <div className="panel-indicator" style={{ backgroundColor: '#d4a000', boxShadow: '0 0 6px #d4a00066' }} />
                    <span>Coherence Critic</span>
                  </div>
                </div>
                <div className="panel-body flex-1 overflow-y-auto">
                  <CriticDashboard />
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* System Status Bar — persistent bottom */}
      <SystemStatusBar health={health.data || null} graph={graph.data || null} />
    </div>
  )
}
