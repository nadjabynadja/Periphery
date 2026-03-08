import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

export function IngestPanel() {
  const queryClient = useQueryClient()
  const [content, setContent] = useState('')
  const [contentType, setContentType] = useState('text/plain')
  const [isIngesting, setIsIngesting] = useState(false)
  const [lastResult, setLastResult] = useState<{ count: number } | null>(null)

  const { data: stats } = useQuery({
    queryKey: ['ingestStats'],
    queryFn: api.getIngestStats,
    refetchInterval: 10000,
  })

  const handleIngest = async () => {
    if (!content.trim()) return
    setIsIngesting(true)
    try {
      const result = await api.ingest(content, contentType)
      setLastResult({ count: result.count })
      setContent('')
      queryClient.invalidateQueries({ queryKey: ['ingestStats'] })
      queryClient.invalidateQueries({ queryKey: ['graph'] })
    } finally {
      setIsIngesting(false)
    }
  }

  return (
    <div className="space-y-3">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-2 p-2 bg-base-800" style={{ borderRadius: '2px', border: '1px solid #1e294055' }}>
          <div>
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider">Docs</div>
            <div className="data-value text-xs">{stats.total_documents}</div>
          </div>
          <div>
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider">Vectors</div>
            <div className="data-value text-xs">{stats.total_vectors}</div>
          </div>
          <div>
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider">Dim</div>
            <div className="data-value text-xs">{stats.embedding_dim}</div>
          </div>
        </div>
      )}

      {/* Content type selector */}
      <div className="flex gap-1">
        {['text/plain', 'application/json', 'text/csv'].map((t) => {
          const label = t.split('/')[1].toUpperCase()
          const active = contentType === t
          return (
            <button
              key={t}
              onClick={() => setContentType(t)}
              className={`px-2 py-0.5 text-xxs font-display font-semibold tracking-wider uppercase transition-all duration-150 ${
                active
                  ? 'text-accent-cyan bg-accent-cyan/10 border-accent-cyan/30'
                  : 'text-text-dim hover:text-text-secondary border-surface-border'
              }`}
              style={{ borderRadius: '2px', border: '1px solid' }}
            >
              {label}
            </button>
          )
        })}
      </div>

      {/* Textarea */}
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={
          contentType === 'text/plain'
            ? 'paste data for ingestion...'
            : contentType === 'application/json'
              ? '{"key": "value", ...}'
              : 'col1,col2\nval1,val2'
        }
        className="command-input w-full resize-y"
        rows={8}
        style={{ minHeight: '80px', fontFamily: '"JetBrains Mono", monospace', fontSize: '0.7rem' }}
      />

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={handleIngest}
          className="btn-primary"
          disabled={isIngesting || !content.trim()}
        >
          {isIngesting ? (
            <span className="flex items-center gap-2">
              <div className="calibrating" style={{ width: '20px' }} />
              INGESTING
            </span>
          ) : 'INGEST'}
        </button>
        {lastResult && (
          <span className="text-xxs font-mono text-accent-cyan">
            +{lastResult.count} chunks ingested
          </span>
        )}
      </div>
    </div>
  )
}
