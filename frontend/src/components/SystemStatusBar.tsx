import { useMemo } from 'react'
import { useStore } from '../store'
import type { ConnectionStatus } from '../api/types'

function formatTimeAgo(isoString: string | null | undefined): string {
  if (!isoString) return 'N/A'
  const diffMs = Date.now() - new Date(isoString).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return '<1m ago'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m ago`
}

function connectionTooltip(status: ConnectionStatus): string {
  switch (status) {
    case 'connected': return 'WebSocket connected'
    case 'reconnecting': return 'Attempting to reconnect...'
    case 'disconnected': return 'Connection lost'
  }
}

function lagColor(seconds: number): string {
  if (seconds < 120) return 'var(--accent-cyan, #00d4ff)'
  if (seconds <= 600) return 'var(--accent-amber, #ffb833)'
  return 'var(--accent-red, #ff4444)'
}

export function SystemStatusBar() {
  const connectionStatus = useStore((s) => s.connectionStatus)
  const snapshot = useStore((s) => s.snapshot)
  const pipelineStats = useStore((s) => s.pipelineStats)
  const health = useStore((s) => s.health)
  const criticMonitoring = useStore((s) => s.criticMonitoring)
  const setShowGraphSettings = useStore((s) => s.setShowGraphSettings)

  const entityCount = snapshot?.entity_count ?? 0
  const relationshipCount = snapshot?.relationship_count ?? 0
  const clusterCount = snapshot?.cluster_count ?? 0
  const lagSeconds = pipelineStats?.pipeline_lag_seconds ?? 0

  const activeFeedCount = useMemo(() => {
    if (!pipelineStats?.stages) return 0
    return pipelineStats.stages.filter((s) => s.status === 'healthy').length
  }, [pipelineStats])

  const tickerText = useMemo(() => {
    const parts: string[] = []
    if (snapshot?.entities?.length) {
      const latest = snapshot.entities
        .slice()
        .sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime())
        .slice(0, 5)
      latest.forEach((e) => parts.push(`[${e.entity_type}] ${e.name}`))
    }
    if (snapshot?.anomalies?.length) {
      snapshot.anomalies.slice(0, 3).forEach((a) => parts.push(`ANOMALY: ${a.description}`))
    }
    return parts.length > 0 ? parts.join('  ///  ') : 'Awaiting data...'
  }, [snapshot])

  return (
    <div
      className="flex items-center justify-between border-b border-surface-border bg-base-800 shrink-0 select-none"
      style={{ height: 32, padding: '0 10px', fontSize: 11, fontFamily: 'var(--font-mono)' }}
    >
      {/* ---- LEFT ---- */}
      <div className="flex items-center gap-3" style={{ minWidth: 0 }}>
        {/* Logo */}
        <div className="flex items-center gap-1.5">
          <span
            style={{
              width: 6,
              height: 6,
              background: '#00d4ff',
              boxShadow: '0 0 6px #00d4ff88',
              display: 'inline-block',
              flexShrink: 0,
            }}
          />
          <span
            className="data-readout"
            style={{
              color: '#00d4ff88',
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              fontSize: 10,
            }}
          >
            PERIPHERY
          </span>
        </div>

        <div className="h-3 w-px bg-surface-border" />

        {/* Connection status */}
        <div className="flex items-center gap-1" title={connectionTooltip(connectionStatus)}>
          <div className={`status-dot ${connectionStatus}`} />
          <span
            className="data-readout"
            style={{
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color:
                connectionStatus === 'connected'
                  ? 'var(--accent-green, #00cc66)'
                  : connectionStatus === 'reconnecting'
                    ? 'var(--accent-amber, #ffb833)'
                    : 'var(--accent-red, #ff4444)',
            }}
          >
            {connectionStatus}
          </span>
        </div>

        <div className="h-3 w-px bg-surface-border" />

        {/* Pipeline lag */}
        <div className="flex items-center gap-1">
          <span className="data-readout" style={{ color: '#6b7a8d', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Lag:
          </span>
          <span className="data-value" style={{ color: lagColor(lagSeconds), fontSize: 11 }}>
            {Math.round(lagSeconds)}s
          </span>
        </div>
      </div>

      {/* ---- CENTER ---- */}
      <div className="flex items-center gap-3" style={{ minWidth: 0, overflow: 'hidden', flex: '1 1 auto', justifyContent: 'center' }}>
        {/* Corpus stats */}
        <span className="data-readout" style={{ color: '#8b9bb4', whiteSpace: 'nowrap' }}>
          {entityCount.toLocaleString()} entities &middot; {relationshipCount.toLocaleString()} relationships &middot; {clusterCount.toLocaleString()} clusters
        </span>

        <div className="h-3 w-px bg-surface-border" />

        {/* Ticker */}
        <div style={{ overflow: 'hidden', maxWidth: 260, flex: '0 1 auto' }}>
          <span
            className="ticker-text data-readout"
            style={{ color: '#5a6a7e', display: 'inline-block' }}
          >
            {tickerText}
          </span>
        </div>
      </div>

      {/* ---- RIGHT ---- */}
      <div className="flex items-center gap-3" style={{ minWidth: 0, whiteSpace: 'nowrap' }}>
        {/* Crystallizer last run */}
        <div className="flex items-center gap-1">
          <span className="data-readout" style={{ color: '#6b7a8d', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Last run:
          </span>
          <span className="data-value" style={{ fontSize: 11 }}>
            {formatTimeAgo(health?.last_crystallization)}
          </span>
        </div>

        <div className="h-3 w-px bg-surface-border" />

        {/* Critic info */}
        <span className="data-readout" style={{ color: '#8b9bb4' }}>
          Critic v{criticMonitoring?.model_version ?? '—'} &middot; Mean: {criticMonitoring?.mean_confidence?.toFixed(2) ?? '—'}
        </span>

        <div className="h-3 w-px bg-surface-border" />

        {/* Active feeds */}
        <div className="flex items-center gap-1">
          <span className="data-readout" style={{ color: '#6b7a8d', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Feeds:
          </span>
          <span className="data-value" style={{ fontSize: 11 }}>
            {activeFeedCount}
          </span>
        </div>

        <div className="h-3 w-px bg-surface-border" />

        {/* Settings gear */}
        <button
          onClick={() => setShowGraphSettings(true)}
          className="flex items-center justify-center"
          style={{
            width: 20,
            height: 20,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: '#6b7a8d',
            padding: 0,
          }}
          title="Graph Settings"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </div>
  )
}
