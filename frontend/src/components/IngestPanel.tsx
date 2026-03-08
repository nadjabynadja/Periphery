import React, { useState } from 'react'
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
    } finally {
      setIsIngesting(false)
    }
  }

  return (
    <div>
      <div style={styles.header}>
        <h2 style={styles.title}>Ingest Data</h2>
        {stats && (
          <span style={styles.stats}>
            {stats.total_documents} documents / {stats.total_vectors} vectors / {stats.embedding_dim}d
          </span>
        )}
      </div>

      <div style={styles.form}>
        <div style={styles.typeSelector}>
          {['text/plain', 'application/json', 'text/csv'].map((t) => (
            <button
              key={t}
              onClick={() => setContentType(t)}
              style={styles.typeButton(contentType === t)}
            >
              {t.split('/')[1].toUpperCase()}
            </button>
          ))}
        </div>

        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={
            contentType === 'text/plain'
              ? 'Paste any text here...'
              : contentType === 'application/json'
                ? '{"key": "value", ...}'
                : 'col1,col2\nval1,val2'
          }
          style={styles.textarea}
          rows={12}
        />

        <div style={styles.actions}>
          <button onClick={handleIngest} style={styles.ingestButton} disabled={isIngesting || !content.trim()}>
            {isIngesting ? 'Ingesting...' : 'Ingest'}
          </button>
          {lastResult && <span style={styles.result}>Ingested {lastResult.count} chunks</span>}
        </div>
      </div>
    </div>
  )
}

const styles = {
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  } as React.CSSProperties,
  title: { fontSize: 18, fontWeight: 400, color: '#ccc', margin: 0 } as React.CSSProperties,
  stats: { fontSize: 13, color: '#888' } as React.CSSProperties,
  form: {} as React.CSSProperties,
  typeSelector: {
    display: 'flex',
    gap: 4,
    marginBottom: 12,
  } as React.CSSProperties,
  typeButton: (active: boolean) =>
    ({
      padding: '4px 12px',
      border: `1px solid ${active ? '#4444aa' : '#333'}`,
      borderRadius: 4,
      backgroundColor: active ? '#1a1a3e' : 'transparent',
      color: active ? '#8888ff' : '#666',
      cursor: 'pointer',
      fontSize: 12,
    }) as React.CSSProperties,
  textarea: {
    width: '100%',
    padding: 16,
    fontSize: 14,
    fontFamily: 'monospace',
    border: '1px solid #2a2a3e',
    borderRadius: 8,
    backgroundColor: '#12121f',
    color: '#e0e0e0',
    outline: 'none',
    resize: 'vertical' as const,
    boxSizing: 'border-box' as const,
  } as React.CSSProperties,
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginTop: 12,
  } as React.CSSProperties,
  ingestButton: {
    padding: '10px 24px',
    border: 'none',
    borderRadius: 6,
    backgroundColor: '#4444aa',
    color: '#fff',
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: 600,
  } as React.CSSProperties,
  result: { fontSize: 13, color: '#4caf50' } as React.CSSProperties,
}
