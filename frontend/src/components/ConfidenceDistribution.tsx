import { useMemo } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import type { Cluster, CriticScore } from '../api'

interface Props {
  clusters: Cluster[]
  criticScores: CriticScore[]
}

function getConfidenceColor(score: number): string {
  if (score > 0.7) return '#00d4ff'
  if (score > 0.4) return '#d4a000'
  return '#ff3333'
}

export function ConfidenceDistribution({ clusters, criticScores }: Props) {
  const chartData = useMemo(() => {
    if (clusters.length === 0) return []

    // Build histogram buckets: 0-10%, 10-20%, ..., 90-100%
    const buckets = Array.from({ length: 10 }, (_, i) => ({
      range: `${i * 10}-${(i + 1) * 10}`,
      count: 0,
      label: `${i * 10}%`,
    }))

    // Map critic scores by cluster id
    const scoreMap = new Map(criticScores.map(s => [s.cluster_id, s.coherence_score]))

    clusters.forEach(cluster => {
      const score = scoreMap.get(cluster.id) ?? cluster.coherence_score ?? 0
      const bucket = Math.min(9, Math.floor(score * 10))
      buckets[bucket].count++
    })

    return buckets
  }, [clusters, criticScores])

  const summaryStats = useMemo(() => {
    if (clusters.length === 0) return null
    const scoreMap = new Map(criticScores.map(s => [s.cluster_id, s.coherence_score]))
    const scores = clusters.map(c => scoreMap.get(c.id) ?? c.coherence_score ?? 0)
    const avg = scores.reduce((a, b) => a + b, 0) / scores.length
    const high = scores.filter(s => s > 0.7).length
    const low = scores.filter(s => s < 0.4).length
    return { avg, high, low, total: scores.length }
  }, [clusters, criticScores])

  if (clusters.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <span className="text-xxs text-text-dim">No cluster data for confidence analysis</span>
      </div>
    )
  }

  return (
    <div className="flex gap-4 h-full items-center">
      {/* Summary metrics */}
      <div className="flex flex-col gap-1 shrink-0" style={{ width: '140px' }}>
        {summaryStats && (
          <>
            <div className="flex justify-between items-baseline">
              <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Avg Confidence</span>
              <span className="data-value text-sm">{(summaryStats.avg * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-xxs text-text-dim">High (&gt;70%)</span>
              <span className="text-xxs font-mono text-accent-cyan">{summaryStats.high}</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-xxs text-text-dim">Low (&lt;40%)</span>
              <span className="text-xxs font-mono text-accent-red">{summaryStats.low}</span>
            </div>
            <div className="flex justify-between items-baseline">
              <span className="text-xxs text-text-dim">Total clusters</span>
              <span className="text-xxs font-mono text-text-secondary">{summaryStats.total}</span>
            </div>
          </>
        )}
      </div>

      {/* Histogram */}
      <div className="flex-1 h-full" style={{ minHeight: '60px' }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <XAxis
              dataKey="label"
              tick={{ fill: '#4a5568', fontSize: 8, fontFamily: 'JetBrains Mono' }}
              axisLine={{ stroke: '#1e2940' }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#4a5568', fontSize: 8, fontFamily: 'JetBrains Mono' }}
              axisLine={{ stroke: '#1e2940' }}
              tickLine={false}
              width={20}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#0d1220',
                border: '1px solid #1e2940',
                borderRadius: '2px',
                fontSize: '10px',
                fontFamily: 'JetBrains Mono',
                color: '#c8cdd5',
              }}
              cursor={{ fill: '#1e294033' }}
            />
            <Bar dataKey="count" radius={[1, 1, 0, 0]}>
              {chartData.map((entry, index) => (
                <Cell
                  key={entry.range}
                  fill={getConfidenceColor((index + 0.5) / 10)}
                  fillOpacity={0.7}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
