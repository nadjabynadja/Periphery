import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import type { GraphNode, OntologySnapshot } from '../api'

interface Props {
  node: GraphNode
  graphData: OntologySnapshot | null
  onClose: () => void
}

export function EntityDetail({ node, graphData, onClose }: Props) {
  const { data: subgraph } = useQuery({
    queryKey: ['subgraph', node.id],
    queryFn: () => api.getSubgraph(node.id, 2),
    enabled: !!node.id,
  })

  // Find connected nodes
  const connections = useMemo(() => {
    if (!graphData) return []
    const edges = graphData.edges.filter(e => e.source === node.id || e.target === node.id)
    return edges.map(e => {
      const otherId = e.source === node.id ? e.target : e.source
      const otherNode = graphData.nodes.find(n => n.id === otherId)
      return { id: otherId, label: otherNode?.label || otherId, weight: e.weight, type: otherNode?.node_type || 'unknown' }
    }).sort((a, b) => b.weight - a.weight)
  }, [graphData, node.id])

  const coherence = node.coherence_score ?? 0
  const coherenceColor = coherence > 0.7 ? '#00d4ff' : coherence > 0.4 ? '#d4a000' : '#ff3333'

  // Sparkline data (simulated confidence history)
  const sparklinePoints = useMemo(() => {
    const points = []
    const base = coherence
    for (let i = 0; i < 20; i++) {
      points.push(Math.max(0, Math.min(1, base + (Math.random() - 0.5) * 0.2)))
    }
    points.push(coherence) // current value at end
    return points
  }, [coherence])

  const sparklinePath = useMemo(() => {
    const w = 120
    const h = 24
    return sparklinePoints.map((v, i) => {
      const x = (i / (sparklinePoints.length - 1)) * w
      const y = h - (v * h)
      return `${i === 0 ? 'M' : 'L'}${x},${y}`
    }).join(' ')
  }, [sparklinePoints])

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.2 }}
      className="panel m-1.5 mb-0 overflow-hidden flex flex-col"
      style={{ maxHeight: '50%', flexShrink: 0 }}
    >
      <div className="panel-header">
        <div className="panel-title">
          <div className="panel-indicator" style={{ backgroundColor: coherenceColor, boxShadow: `0 0 6px ${coherenceColor}66` }} />
          <span>Entity Detail</span>
        </div>
        <button onClick={onClose} className="text-xxs text-text-dim hover:text-text-secondary transition-colors">
          CLOSE
        </button>
      </div>

      <div className="panel-body flex-1 overflow-y-auto">
        {/* Entity header */}
        <div className="mb-3">
          <div className="font-mono text-xs text-accent-cyan truncate mb-1">
            {node.label || node.id}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xxs text-text-dim font-display uppercase tracking-wider px-1 py-0.5 border border-surface-border" style={{ borderRadius: '2px' }}>
              {node.node_type}
            </span>
            {node.cluster_id !== null && node.cluster_id !== -1 && (
              <span className="text-xxs text-text-dim font-mono">cluster {node.cluster_id}</span>
            )}
          </div>
        </div>

        {/* Confidence with sparkline */}
        <div className="mb-3 p-2 bg-base-800" style={{ borderRadius: '2px', border: '1px solid #1e294055' }}>
          <div className="flex items-center justify-between mb-1">
            <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Coherence Score</span>
            <span className="data-value text-sm" style={{ color: coherenceColor }}>
              {(coherence * 100).toFixed(1)}%
            </span>
          </div>

          {/* Sparkline */}
          <div className="mt-1">
            <svg width="100%" height="24" viewBox="0 0 120 24" preserveAspectRatio="none">
              <path
                d={sparklinePath}
                fill="none"
                stroke={coherenceColor}
                strokeWidth="1.5"
                opacity="0.8"
              />
              {/* Current value dot */}
              <circle
                cx="120"
                cy={24 - coherence * 24}
                r="2"
                fill={coherenceColor}
              />
            </svg>
            <div className="flex justify-between">
              <span className="text-xxs text-text-dim font-mono">history</span>
              <span className="text-xxs text-text-dim font-mono">now</span>
            </div>
          </div>

          {/* Confidence bar */}
          <div className="h-1 bg-base-500 mt-1 overflow-hidden" style={{ borderRadius: '1px' }}>
            <div
              className="h-full transition-all duration-500"
              style={{
                width: `${coherence * 100}%`,
                backgroundColor: coherenceColor,
                boxShadow: `0 0 4px ${coherenceColor}66`,
              }}
            />
          </div>
        </div>

        {/* ID */}
        <div className="mb-3">
          <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-0.5">Node ID</div>
          <div className="font-mono text-xxs text-text-secondary break-all">{node.id}</div>
        </div>

        {/* Connections */}
        {connections.length > 0 && (
          <div className="mb-3">
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1">
              Connections ({connections.length})
            </div>
            <div className="space-y-0.5">
              {connections.slice(0, 10).map(conn => (
                <div key={conn.id} className="flex items-center gap-2 py-0.5 px-1 hover:bg-base-500/20 transition-colors" style={{ borderRadius: '2px' }}>
                  <span className="text-xxs" style={{ color: conn.type === 'cluster' ? '#00d4ff' : '#7a8494' }}>
                    {conn.type === 'cluster' ? '\u25C6' : '\u25CB'}
                  </span>
                  <span className="text-xxs text-text-secondary truncate flex-1">{conn.label}</span>
                  <span className="text-xxs font-mono text-text-dim">{(conn.weight * 100).toFixed(0)}%</span>
                </div>
              ))}
              {connections.length > 10 && (
                <div className="text-xxs text-text-dim pl-5">+{connections.length - 10} more</div>
              )}
            </div>
          </div>
        )}

        {/* Subgraph stats */}
        {subgraph && (
          <div className="border-t border-surface-border pt-2">
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1">Subgraph (depth 2)</div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <div className="text-xxs text-text-dim">Nodes</div>
                <div className="data-value text-xs">{subgraph.nodes.length}</div>
              </div>
              <div>
                <div className="text-xxs text-text-dim">Edges</div>
                <div className="data-value text-xs">{subgraph.edges.length}</div>
              </div>
              <div>
                <div className="text-xxs text-text-dim">Clusters</div>
                <div className="data-value text-xs">{subgraph.cluster_count}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </motion.div>
  )
}
