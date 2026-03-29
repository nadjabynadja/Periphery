// ============================================
// SystemStatusBar — top bar with health, pipeline stats, connection, user menu
// ============================================

import React, { useEffect } from 'react'
import { useStore } from '../store'
import { peripheryApi } from '../api/client'
import { UserMenu } from './auth/UserMenu'

export const SystemStatusBar: React.FC = () => {
  const health = useStore((s) => s.health)
  const setHealth = useStore((s) => s.setHealth)
  const pipelineStats = useStore((s) => s.pipelineStats)
  const setPipelineStats = useStore((s) => s.setPipelineStats)
  const connectionStatus = useStore((s) => s.connectionStatus)
  const viewMode = useStore((s) => s.viewMode)
  const setViewMode = useStore((s) => s.setViewMode)
  const searchPanelOpen = useStore((s) => s.searchPanelOpen)
  const setSearchPanelOpen = useStore((s) => s.setSearchPanelOpen)

  // Poll health + pipeline stats
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const [h, p] = await Promise.allSettled([
          peripheryApi.getHealth(),
          peripheryApi.getPipelineStats(),
        ])
        if (h.status === 'fulfilled') setHealth(h.value)
        if (p.status === 'fulfilled') setPipelineStats(p.value)
      } catch {
        // Silently fail
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 30_000)
    return () => clearInterval(interval)
  }, [setHealth, setPipelineStats])

  const healthStatus = health?.status || 'unknown'
  const vectors = health?.vectors ?? 0
  const clusters = health?.clusters ?? 0
  const totalProcessed = pipelineStats?.total_processed ?? 0
  const pipelineLag = pipelineStats?.pipeline_lag_seconds ?? 0

  return (
    <header className="h-9 bg-base-800 border-b border-surface-border flex items-center px-3 gap-4 shrink-0 z-20">
      {/* Brand */}
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-xs font-display font-bold tracking-widest text-accent-cyan uppercase">
          Periphery
        </span>
        <span className="w-px h-4 bg-surface-border" />
      </div>

      {/* Health */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span className={`status-dot ${healthStatus === 'ok' || healthStatus === 'healthy' ? 'healthy' : healthStatus === 'degraded' ? 'degraded' : 'error'}`} />
        <span className="data-readout">{healthStatus.toUpperCase()}</span>
      </div>

      {/* Stats */}
      <div className="flex items-center gap-3 shrink-0">
        <StatusItem label="VEC" value={formatNumber(vectors)} />
        <StatusItem label="CLU" value={String(clusters)} />
        <StatusItem label="DOC" value={formatNumber(totalProcessed)} />
        {pipelineLag > 0 && (
          <StatusItem label="LAG" value={`${pipelineLag.toFixed(0)}s`} warn={pipelineLag > 60} />
        )}
      </div>

      {/* Pipeline stages */}
      {pipelineStats?.stages && pipelineStats.stages.length > 0 && (
        <div className="hidden lg:flex items-center gap-1 shrink-0">
          <span className="w-px h-4 bg-surface-border mr-1" />
          {pipelineStats.stages.slice(0, 4).map((stage) => (
            <div key={stage.name} className="flex items-center gap-1" title={`${stage.name}: ${stage.status} (Q:${stage.queue_size})`}>
              <span className={`status-dot ${stage.status}`} />
              <span className="data-readout text-xxs">{stage.name.slice(0, 6).toUpperCase()}</span>
            </div>
          ))}
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* View mode tabs */}
      <div className="flex items-center gap-0 shrink-0">
        {(['graph', 'map', 'timeline'] as const).map((mode) => (
          <button
            key={mode}
            className={`tab-item !border-b-0 ${viewMode === mode ? 'active' : ''}`}
            onClick={() => setViewMode(mode)}
          >
            {mode === 'graph' ? '◆' : mode === 'map' ? '◈' : '▬'} {mode}
          </button>
        ))}
      </div>

      <span className="w-px h-4 bg-surface-border" />

      {/* Search toggle */}
      <button
        className={`btn-secondary !py-1 !px-2 ${searchPanelOpen ? '!border-accent-cyan/50 !text-accent-cyan' : ''}`}
        onClick={() => setSearchPanelOpen(!searchPanelOpen)}
        title="Toggle search panel"
      >
        ⌕
      </button>

      {/* Connection */}
      <div className="flex items-center gap-1 shrink-0">
        <span className={`status-dot ${connectionStatus}`} />
        <span className="data-readout text-xxs">{connectionStatus === 'connected' ? 'LIVE' : connectionStatus.toUpperCase()}</span>
      </div>

      {/* User menu */}
      <UserMenu />
    </header>
  )
}

const StatusItem: React.FC<{ label: string; value: string; warn?: boolean }> = ({
  label,
  value,
  warn,
}) => (
  <div className="flex items-center gap-1">
    <span className="data-readout text-xxs">{label}</span>
    <span className={`data-value text-xxs ${warn ? '!text-accent-amber' : ''}`}>{value}</span>
  </div>
)

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export default SystemStatusBar
