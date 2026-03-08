import React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

export function ClusterView() {
  const queryClient = useQueryClient()

  const { data: clusters, isLoading } = useQuery({
    queryKey: ['clusters'],
    queryFn: api.getClusters,
    refetchInterval: 30000,
  })

  const handleCrystallize = async () => {
    await api.triggerCrystallize()
    queryClient.invalidateQueries({ queryKey: ['clusters'] })
  }

  if (isLoading) return <div style={styles.loading}>Loading clusters...</div>

  if (!clusters || clusters.length === 0) {
    return (
      <div style={styles.empty}>
        <p>No clusters detected yet.</p>
        <button onClick={handleCrystallize} style={styles.button}>
          Crystallize Now
        </button>
      </div>
    )
  }

  return (
    <div>
      <div style={styles.header}>
        <h2 style={styles.title}>{clusters.length} Emergent Clusters</h2>
        <button onClick={handleCrystallize} style={styles.button}>
          Re-crystallize
        </button>
      </div>

      <div style={styles.grid}>
        {clusters.map((cluster) => {
          const score = cluster.coherence_score ?? 0
          const scoreColor = score > 0.7 ? '#4caf50' : score > 0.4 ? '#ff9800' : '#f44336'

          return (
            <div key={cluster.id} style={styles.card}>
              <div style={styles.cardHeader}>
                <span style={styles.clusterId}>Cluster {cluster.id}</span>
                <span style={{ ...styles.score, color: scoreColor }}>
                  {(score * 100).toFixed(0)}% coherent
                </span>
              </div>
              <div style={styles.cardBody}>
                <span style={styles.docCount}>{cluster.document_ids.length} documents</span>
                {cluster.label && <span style={styles.label}>{cluster.label}</span>}
              </div>
              {/* Coherence bar */}
              <div style={styles.bar}>
                <div
                  style={{
                    ...styles.barFill,
                    width: `${score * 100}%`,
                    backgroundColor: scoreColor,
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles = {
  loading: { textAlign: 'center' as const, color: '#666', padding: 40 } as React.CSSProperties,
  empty: { textAlign: 'center' as const, color: '#666', padding: 60 } as React.CSSProperties,
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  } as React.CSSProperties,
  title: { fontSize: 18, fontWeight: 400, color: '#ccc', margin: 0 } as React.CSSProperties,
  button: {
    padding: '6px 16px',
    border: '1px solid #333',
    borderRadius: 6,
    backgroundColor: '#1a1a2e',
    color: '#aaa',
    cursor: 'pointer',
    fontSize: 13,
  } as React.CSSProperties,
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: 12,
  } as React.CSSProperties,
  card: {
    backgroundColor: '#12121f',
    border: '1px solid #1a1a2e',
    borderRadius: 8,
    padding: 16,
  } as React.CSSProperties,
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: 8,
  } as React.CSSProperties,
  clusterId: { fontSize: 14, fontWeight: 600, color: '#8888ff' } as React.CSSProperties,
  score: { fontSize: 12 } as React.CSSProperties,
  cardBody: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: 12,
  } as React.CSSProperties,
  docCount: { fontSize: 13, color: '#888' } as React.CSSProperties,
  label: { fontSize: 12, color: '#666', fontStyle: 'italic' } as React.CSSProperties,
  bar: {
    height: 4,
    backgroundColor: '#1a1a2e',
    borderRadius: 2,
    overflow: 'hidden',
  } as React.CSSProperties,
  barFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.5s ease',
  } as React.CSSProperties,
}
