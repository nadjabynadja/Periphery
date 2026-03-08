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

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="calibrating" style={{ width: '60px' }} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Train button */}
      <button onClick={handleTrain} className="btn-primary w-full">
        Train Critic (10 epochs)
      </button>

      {/* Coherence scores */}
      {scores && scores.length > 0 ? (
        <div>
          <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1.5">
            Cluster Coherence
          </div>
          <div className="space-y-0.5">
            {scores
              .sort((a, b) => b.coherence_score - a.coherence_score)
              .map((s) => {
                const pct = s.coherence_score * 100
                const color = pct > 70 ? '#00d4ff' : pct > 40 ? '#d4a000' : '#ff3333'
                return (
                  <div key={s.cluster_id} className="flex items-center gap-2 py-1 px-1 hover:bg-base-500/20 transition-colors" style={{ borderRadius: '2px' }}>
                    <span className="text-xxs text-text-dim font-mono w-10 shrink-0">C{s.cluster_id}</span>
                    <div className="flex-1 h-1 bg-base-500 overflow-hidden" style={{ borderRadius: '1px' }}>
                      <div
                        className="h-full transition-all duration-300"
                        style={{ width: `${pct}%`, backgroundColor: color, boxShadow: `0 0 4px ${color}44` }}
                      />
                    </div>
                    <span className="text-xxs font-mono w-8 text-right shrink-0" style={{ color }}>
                      {pct.toFixed(0)}%
                    </span>
                    <span className="text-xxs text-text-dim font-mono w-8 shrink-0">{s.document_count}d</span>
                  </div>
                )
              })}
          </div>
        </div>
      ) : (
        <div className="py-4 text-center">
          <div className="text-xxs text-text-dim">No coherence scores. Ingest data, crystallize, then train.</div>
        </div>
      )}

      {/* Outliers */}
      {outliers && outliers.outliers.length > 0 && (
        <div className="border-t border-surface-border pt-2">
          <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1.5">
            Outlier Documents
          </div>
          <div className="space-y-0.5">
            {outliers.outliers.map((o, i) => (
              <div key={o.document_id} className="flex items-center gap-2 py-0.5 px-1" style={{ borderRadius: '2px' }}>
                <span className="text-xxs text-text-dim font-mono w-4">{i + 1}</span>
                <span className="text-xxs font-mono text-text-secondary truncate flex-1">
                  {o.document_id.substring(0, 12)}...
                </span>
                <span className="text-xxs text-text-dim font-mono">
                  {o.cluster_id === -1 ? 'noise' : `C${o.cluster_id}`}
                </span>
                <span className="text-xxs font-mono text-accent-red">
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
