import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { useState, useEffect } from 'react'

interface FeedEntry {
  id: string
  type: 'ingest' | 'crystallize' | 'critic' | 'query'
  message: string
  timestamp: Date
  confidence?: number
}

export function DataFeed() {
  const [entries, setEntries] = useState<FeedEntry[]>([])

  const { data: stats } = useQuery({
    queryKey: ['ingestStats'],
    queryFn: api.getIngestStats,
    refetchInterval: 5000,
  })

  const { data: crystalStats } = useQuery({
    queryKey: ['crystallizerStats'],
    queryFn: api.getCrystallizerStats,
    refetchInterval: 10000,
  })

  // Generate feed entries from changing stats
  useEffect(() => {
    if (!stats) return
    setEntries(prev => {
      const exists = prev.some(e => e.id === `ingest-${stats.total_documents}`)
      if (exists) return prev
      const entry: FeedEntry = {
        id: `ingest-${stats.total_documents}`,
        type: 'ingest',
        message: `${stats.total_documents} documents indexed, ${stats.total_vectors} vectors stored (${stats.embedding_dim}d)`,
        timestamp: new Date(),
      }
      return [entry, ...prev].slice(0, 50)
    })
  }, [stats])

  useEffect(() => {
    if (!crystalStats) return
    const key = JSON.stringify(crystalStats)
    setEntries(prev => {
      const exists = prev.some(e => e.id === `crystal-${key}`)
      if (exists) return prev
      const entry: FeedEntry = {
        id: `crystal-${key}`,
        type: 'crystallize',
        message: `Crystallizer state updated`,
        timestamp: new Date(),
      }
      return [entry, ...prev].slice(0, 50)
    })
  }, [crystalStats])

  const typeIcon = (type: FeedEntry['type']) => {
    switch (type) {
      case 'ingest': return '\u25C9'
      case 'crystallize': return '\u25C6'
      case 'critic': return '\u25B2'
      case 'query': return '\u25B6'
    }
  }

  const typeColor = (type: FeedEntry['type']) => {
    switch (type) {
      case 'ingest': return '#00d4ff'
      case 'crystallize': return '#10b981'
      case 'critic': return '#d4a000'
      case 'query': return '#8b5cf6'
    }
  }

  return (
    <div className="space-y-0.5">
      {entries.length === 0 ? (
        <div className="py-8 text-center">
          <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-2">Awaiting data</div>
          <div className="text-xxs text-text-dim">Feed will populate as the system processes data</div>
        </div>
      ) : (
        entries.map(entry => (
          <div key={entry.id} className="flex items-start gap-2 py-1.5 px-1 hover:bg-base-500/20 transition-colors" style={{ borderRadius: '2px' }}>
            <span className="text-xxs shrink-0 mt-0.5" style={{ color: typeColor(entry.type) }}>
              {typeIcon(entry.type)}
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-xxs text-text-secondary m-0 leading-snug">{entry.message}</p>
              <span className="text-xxs text-text-dim font-mono">
                {entry.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
            </div>
            {entry.confidence !== undefined && (
              <span className="text-xxs font-mono shrink-0" style={{
                color: entry.confidence > 0.7 ? '#00d4ff' : entry.confidence > 0.4 ? '#d4a000' : '#ff3333'
              }}>
                {(entry.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
        ))
      )}

      {/* Live stats footer */}
      {stats && (
        <div className="border-t border-surface-border pt-2 mt-2">
          <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1">System Metrics</div>
          <div className="grid grid-cols-2 gap-1">
            <div className="flex justify-between">
              <span className="text-xxs text-text-dim">Documents</span>
              <span className="data-value text-xxs">{stats.total_documents}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-xxs text-text-dim">Vectors</span>
              <span className="data-value text-xxs">{stats.total_vectors}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-xxs text-text-dim">Dimensions</span>
              <span className="data-value text-xxs">{stats.embedding_dim}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
