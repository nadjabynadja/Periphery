import { useState, useEffect } from 'react'
import type { OntologySnapshot } from '../api'

interface HealthData {
  status: string
  vectors: number
  clusters: number
  last_crystallization: string | null
}

interface Props {
  health: HealthData | null
  graph: OntologySnapshot | null
}

export function SystemStatusBar({ health, graph }: Props) {
  const [uptime, setUptime] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => setUptime(u => u + 1), 1000)
    return () => clearInterval(interval)
  }, [])

  const formatUptime = (seconds: number): string => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }

  const isHealthy = health?.status === 'healthy'

  return (
    <div className="flex items-center justify-between px-3 py-1 border-t border-surface-border bg-base-800 shrink-0">
      {/* Left: System status */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <div className={`status-dot ${isHealthy ? 'online' : 'error'}`} />
          <span className="text-xxs font-display font-semibold tracking-wider uppercase" style={{ color: isHealthy ? '#00d4ff' : '#ff3333' }}>
            {isHealthy ? 'SYSTEM ONLINE' : 'SYSTEM OFFLINE'}
          </span>
        </div>

        <div className="h-3 w-px bg-surface-border" />

        <div className="flex items-center gap-1">
          <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Uptime</span>
          <span className="text-xxs font-mono text-text-secondary">{formatUptime(uptime)}</span>
        </div>
      </div>

      {/* Center: Data metrics */}
      <div className="flex items-center gap-4">
        <MetricChip label="VECTORS" value={health?.vectors ?? 0} />
        <MetricChip label="CLUSTERS" value={health?.clusters ?? 0} />
        <MetricChip label="NODES" value={graph?.nodes.length ?? 0} />
        <MetricChip label="EDGES" value={graph?.edges.length ?? 0} />
      </div>

      {/* Right: Crystallizer status */}
      <div className="flex items-center gap-3">
        {health?.last_crystallization ? (
          <div className="flex items-center gap-1">
            <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Last crystal</span>
            <span className="text-xxs font-mono text-text-secondary">
              {new Date(health.last_crystallization).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ) : (
          <span className="text-xxs text-text-dim font-display uppercase tracking-wider">No crystallization</span>
        )}

        <div className="h-3 w-px bg-surface-border" />

        <div className="flex items-center gap-1">
          <div className={`status-dot ${isHealthy ? 'online' : 'offline'}`} />
          <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Crystallizer</span>
        </div>

        <div className="flex items-center gap-1">
          <div className={`status-dot ${isHealthy ? 'online' : 'offline'}`} />
          <span className="text-xxs text-text-dim font-display uppercase tracking-wider">Critic</span>
        </div>
      </div>
    </div>
  )
}

function MetricChip({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xxs text-text-dim font-display uppercase tracking-wider">{label}</span>
      <span className="data-value">{value.toLocaleString()}</span>
    </div>
  )
}
