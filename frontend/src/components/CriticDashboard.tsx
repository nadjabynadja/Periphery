import React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

export function CriticDashboard() {
  const queryClient = useQueryClient()

  const { data: scores, isLoading } = useQuery({
    queryKey: ['criticScores'],
    queryFn: api.getCriticScores,
    refetchInterval: 30000,
  })

  const { data: outliers } = useQuery({
    queryKey: ['outliers'],
    queryFn: () => api.getOutliers(10),
    refetchInterval: 30000,
  })

  const handleTrain = async () => {
    await api.trainCritic(10)
    queryClient.invalidateQueries({ queryKey: ['criticScores'] })
    queryClient.invalidateQueries({ queryKey: ['outliers'] })
  }

  if (isLoading) return <div style={styles.loading}>Loading critic scores...</div>

  return (
    <div>
      <div style={styles.header}>
        <h2 style={styles.title}>Coherence Critic</h2>
        <button onClick={handleTrain} style={styles.button}>
          Train Critic (10 epochs)
        </button>
      </div>

      {/* Scores heatmap */}
      {scores && scores.length > 0 ? (
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>Cluster Coherence Scores</h3>
          <div style={styles.scoreList}>
            {scores
              .sort((a, b) => b.coherence_score - a.coherence_score)
              .map((s) => {
                const pct = s.coherence_score * 100
                const color = pct > 70 ? '#4caf50' : pct > 40 ? '#ff9800' : '#f44336'
                return (
                  <div key={s.cluster_id} style={styles.scoreRow}>
                    <span style={styles.scoreLabel}>Cluster {s.cluster_id}</span>
                    <div style={styles.scoreBarContainer}>
                      <div style={{ ...styles.scoreBarFill, width: `${pct}%`, backgroundColor: color }} />
                    </div>
                    <span style={{ ...styles.scoreValue, color }}>{pct.toFixed(0)}%</span>
                    <span style={styles.docCount}>{s.document_count} docs</span>
                  </div>
                )
              })}
          </div>
        </div>
      ) : (
        <div style={styles.empty}>
          <p>No coherence scores yet. Ingest data, crystallize, then train the critic.</p>
        </div>
      )}

      {/* Outliers */}
      {outliers && outliers.outliers.length > 0 && (
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>Outlier Documents (Lowest Coherence)</h3>
          <div style={styles.outlierList}>
            {outliers.outliers.map((o, i) => (
              <div key={o.document_id} style={styles.outlierRow}>
                <span style={styles.outlierIndex}>{i + 1}</span>
                <span style={styles.outlierId}>{o.document_id.substring(0, 12)}...</span>
                <span style={styles.outlierCluster}>
                  {o.cluster_id === -1 ? 'noise' : `cluster ${o.cluster_id}`}
                </span>
                <span style={{ ...styles.outlierScore, color: '#f44336' }}>
                  {(o.coherence_score * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const styles = {
  loading: { textAlign: 'center' as const, color: '#666', padding: 40 } as React.CSSProperties,
  empty: { textAlign: 'center' as const, color: '#666', padding: 40 } as React.CSSProperties,
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
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
  section: { marginBottom: 32 } as React.CSSProperties,
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#888',
    textTransform: 'uppercase' as const,
    letterSpacing: 1,
    marginBottom: 12,
  } as React.CSSProperties,
  scoreList: {} as React.CSSProperties,
  scoreRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 12px',
    backgroundColor: '#12121f',
    borderRadius: 4,
    marginBottom: 4,
  } as React.CSSProperties,
  scoreLabel: { fontSize: 13, color: '#aaa', width: 80, flexShrink: 0 } as React.CSSProperties,
  scoreBarContainer: {
    flex: 1,
    height: 8,
    backgroundColor: '#1a1a2e',
    borderRadius: 4,
    overflow: 'hidden',
  } as React.CSSProperties,
  scoreBarFill: {
    height: '100%',
    borderRadius: 4,
    transition: 'width 0.3s ease',
  } as React.CSSProperties,
  scoreValue: { fontSize: 13, fontWeight: 600, width: 40, textAlign: 'right' as const } as React.CSSProperties,
  docCount: { fontSize: 11, color: '#666', width: 50 } as React.CSSProperties,
  outlierList: {} as React.CSSProperties,
  outlierRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '6px 12px',
    backgroundColor: '#12121f',
    borderRadius: 4,
    marginBottom: 2,
    fontSize: 13,
  } as React.CSSProperties,
  outlierIndex: { color: '#555', width: 20 } as React.CSSProperties,
  outlierId: { color: '#888', fontFamily: 'monospace', fontSize: 12 } as React.CSSProperties,
  outlierCluster: { color: '#666', flex: 1 } as React.CSSProperties,
  outlierScore: { fontWeight: 600 } as React.CSSProperties,
}
