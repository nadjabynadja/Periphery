import React, { useCallback, useMemo, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ForceGraph2D from 'react-force-graph-2d'
import { api } from '../api'
import type { OntologySnapshot } from '../api'

export function GraphView() {
  const queryClient = useQueryClient()
  const graphRef = useRef<any>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['graph'],
    queryFn: api.getGraph,
    refetchInterval: 30000,
  })

  const handleCrystallize = async () => {
    await api.triggerCrystallize()
    queryClient.invalidateQueries({ queryKey: ['graph'] })
  }

  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] }
    return {
      nodes: data.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        clusterId: n.cluster_id,
        nodeType: n.node_type,
        coherenceScore: n.coherence_score,
        val: n.node_type === 'cluster' ? 3 : 1,
      })),
      links: data.edges.map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
      })),
    }
  }, [data])

  const clusterColors = useMemo(() => {
    const palette = [
      '#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#8b5cf6',
      '#06b6d4', '#ef4444', '#22c55e', '#f97316', '#a855f7',
    ]
    const colors: Record<number, string> = { [-1]: '#333' }
    if (data) {
      const clusterIds = [...new Set(data.nodes.map((n) => n.cluster_id).filter((c) => c !== null && c !== -1))]
      clusterIds.forEach((id, i) => {
        colors[id!] = palette[i % palette.length]
      })
    }
    return colors
  }, [data])

  const nodeColor = useCallback(
    (node: any) => {
      if (node.nodeType === 'cluster') {
        return clusterColors[node.clusterId] || '#666'
      }
      const base = clusterColors[node.clusterId] || '#444'
      return base + '99' // Add transparency for document nodes
    },
    [clusterColors],
  )

  const nodeLabel = useCallback((node: any) => {
    const parts = [node.label?.substring(0, 80) || node.id]
    if (node.coherenceScore != null) {
      parts.push(`Coherence: ${(node.coherenceScore * 100).toFixed(0)}%`)
    }
    return parts.join('\n')
  }, [])

  if (isLoading) return <div style={styles.loading}>Loading graph...</div>
  if (error) return <div style={styles.error}>Failed to load graph</div>
  if (!data || data.nodes.length === 0) {
    return (
      <div style={styles.empty}>
        <p>No graph data yet. Ingest some data and run crystallization.</p>
        <button onClick={handleCrystallize} style={styles.button}>
          Crystallize Now
        </button>
      </div>
    )
  }

  return (
    <div>
      <div style={styles.toolbar}>
        <span style={styles.stats}>
          {data.cluster_count} clusters / {data.document_count} documents / {data.edges.length} edges
        </span>
        <button onClick={handleCrystallize} style={styles.button}>
          Re-crystallize
        </button>
      </div>
      <div style={styles.graphContainer}>
        <ForceGraph2D
          ref={graphRef}
          graphData={graphData}
          nodeColor={nodeColor}
          nodeLabel={nodeLabel}
          nodeRelSize={4}
          linkColor={() => '#222244'}
          linkWidth={(link: any) => Math.max(0.5, link.weight * 2)}
          backgroundColor="#0a0a0f"
          width={1160}
          height={600}
        />
      </div>
    </div>
  )
}

const styles = {
  loading: { textAlign: 'center' as const, color: '#666', padding: 40 } as React.CSSProperties,
  error: { textAlign: 'center' as const, color: '#f44336', padding: 40 } as React.CSSProperties,
  empty: { textAlign: 'center' as const, color: '#666', padding: 60 } as React.CSSProperties,
  toolbar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
    padding: '8px 12px',
    backgroundColor: '#12121f',
    borderRadius: 8,
  } as React.CSSProperties,
  stats: { fontSize: 13, color: '#888' } as React.CSSProperties,
  button: {
    padding: '6px 16px',
    border: '1px solid #333',
    borderRadius: 6,
    backgroundColor: '#1a1a2e',
    color: '#aaa',
    cursor: 'pointer',
    fontSize: 13,
  } as React.CSSProperties,
  graphContainer: {
    border: '1px solid #1a1a2e',
    borderRadius: 12,
    overflow: 'hidden',
  } as React.CSSProperties,
}
