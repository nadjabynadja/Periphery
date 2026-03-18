import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { peripheryApi } from '../api/client'
import { useStore } from '../store'
import type { PipelineStage, SourceCategory, FeedEntry } from '../api/types'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<SourceCategory, string> = {
  government: '#3b82f6',
  news: '#c8cdd5',
  cyber: '#00CC66',
  academic: '#a855f7',
  conflict: '#FFB833',
}

const CATEGORY_ICONS: Record<SourceCategory, string> = {
  government: '\u2691', // flag
  news: '\u25A3',       // square with fill
  cyber: '\u25C8',      // diamond in circle
  academic: '\u25B3',   // triangle
  conflict: '\u25C9',   // fisheye
}

const QUALITY_COLORS: Record<string, string> = {
  full: '#00D4FF',
  summary: '#FFB833',
  metadata: '#475569',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(timestamp: string): string {
  const diff = Date.now() - new Date(timestamp).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return text.slice(0, max) + '\u2026'
}

function lagColor(seconds: number): string {
  if (seconds < 120) return '#00D4FF'
  if (seconds < 300) return '#FFB833'
  return '#FF4444'
}

function statusDotClass(status: PipelineStage['status']): string {
  switch (status) {
    case 'healthy':  return 'status-dot healthy'
    case 'degraded': return 'status-dot degraded'
    case 'error':    return 'status-dot error'
    default:         return 'status-dot offline'
  }
}

// ---------------------------------------------------------------------------
// Feed entry derivation from API data
// ---------------------------------------------------------------------------

function deriveFeedEntries(
  ingestStats: { total_documents: number; total_vectors: number; embedding_dim: number } | undefined,
  crystalStats: Record<string, unknown> | undefined,
  entities: ReturnType<typeof useStore.getState>['entities'],
): FeedEntry[] {
  const entries: FeedEntry[] = []

  // Derive feed entries from store entities as recent document proxies
  if (entities?.length) {
    for (const entity of entities) {
      const category = inferCategory(entity.entity_type)
      entries.push({
        id: entity.canonical_id,
        title: entity.name,
        source: entity.entity_type,
        category,
        timestamp: entity.last_seen,
        content_quality: entity.source_count > 3 ? 'full' : entity.source_count > 1 ? 'summary' : 'metadata',
        confidence: entity.confidence,
        entity_count: entity.source_count,
      })
    }
  }

  // Sort by timestamp descending
  entries.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
  return entries.slice(0, 80)
}

function inferCategory(entityType: string): SourceCategory {
  const t = entityType.toLowerCase()
  if (t.includes('gov') || t.includes('state') || t.includes('official') || t.includes('agency')) return 'government'
  if (t.includes('cyber') || t.includes('malware') || t.includes('apt') || t.includes('vuln')) return 'cyber'
  if (t.includes('academic') || t.includes('research') || t.includes('university')) return 'academic'
  if (t.includes('conflict') || t.includes('military') || t.includes('weapon') || t.includes('attack')) return 'conflict'
  return 'news'
}

// ---------------------------------------------------------------------------
// Source status helpers
// ---------------------------------------------------------------------------

interface SourceStatusEntry {
  name: string
  status: 'active' | 'degraded' | 'dormant'
  category: SourceCategory
}

function deriveSourceStatus(
  pipelineStats: ReturnType<typeof useStore.getState>['pipelineStats'],
): { active: number; degraded: number; dormant: number; entries: SourceStatusEntry[] } {
  const entries: SourceStatusEntry[] = []

  if (pipelineStats?.stages) {
    for (const stage of pipelineStats.stages) {
      const status: SourceStatusEntry['status'] =
        stage.status === 'healthy' ? 'active' :
        stage.status === 'degraded' ? 'degraded' : 'dormant'
      entries.push({
        name: stage.name,
        status,
        category: inferCategory(stage.name),
      })
    }
  }

  const active = entries.filter(e => e.status === 'active').length
  const degraded = entries.filter(e => e.status === 'degraded').length
  const dormant = entries.filter(e => e.status === 'dormant').length

  return { active, degraded, dormant, entries }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DataFeedSidebar() {
  const snapshot = useStore(s => s.snapshot)
  const entities = useStore(s => s.entities)
  const pipelineStats = useStore(s => s.pipelineStats)
  const [sourceExpanded, setSourceExpanded] = useState(false)

  // Fetch ingest + crystallizer stats via react-query
  const { data: ingestStats } = useQuery({
    queryKey: ['ingestStats'],
    queryFn: () => peripheryApi.getIngestStats(),
    refetchInterval: 5000,
  })

  const { data: crystalStats } = useQuery({
    queryKey: ['crystallizerStats'],
    queryFn: () => peripheryApi.getCrystallizerStats(),
    refetchInterval: 10000,
  })

  // Derive feed entries from store entities
  const feedEntries = useMemo(
    () => deriveFeedEntries(ingestStats, crystalStats, entities),
    [ingestStats, crystalStats, entities],
  )

  // Derive source status
  const sourceStatus = useMemo(
    () => deriveSourceStatus(pipelineStats),
    [pipelineStats],
  )

  const stages = pipelineStats?.stages ?? []
  const lagSeconds = pipelineStats?.pipeline_lag_seconds ?? 0

  return (
    <div className="panel flex flex-col h-full">
      {/* ── RECENT DOCUMENTS FEED ── */}
      <div className="panel-header">
        <div className="panel-title">
          <div className="panel-indicator" />
          <span>Recent Documents</span>
        </div>
        {ingestStats && (
          <span className="data-readout">{ingestStats.total_documents} docs</span>
        )}
      </div>

      <div className="panel-body flex-1 min-h-0 overflow-y-auto space-y-0">
        {feedEntries.length === 0 ? (
          <div className="py-6 text-center">
            <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1">
              Awaiting ingestion
            </div>
            <div className="text-xxs text-text-dim">Feed populates as documents arrive</div>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {feedEntries.map((entry) => (
              <motion.div
                key={entry.id}
                initial={{ opacity: 0, y: -16, height: 0 }}
                animate={{ opacity: 1, y: 0, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
              >
                <div
                  className="flex items-start gap-1.5 py-1 px-1.5 hover:bg-base-500/20 transition-colors"
                  style={{
                    borderLeft: `2px solid ${CATEGORY_COLORS[entry.category]}`,
                    borderRadius: '2px',
                    marginBottom: '1px',
                  }}
                >
                  {/* Source icon */}
                  <span
                    className="shrink-0 mt-px"
                    style={{
                      color: CATEGORY_COLORS[entry.category],
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12px',
                      lineHeight: '1',
                    }}
                  >
                    {CATEGORY_ICONS[entry.category]}
                  </span>

                  {/* Title + source + time */}
                  <div className="flex-1 min-w-0">
                    <div
                      className="text-text-secondary leading-tight"
                      style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}
                      title={entry.title}
                    >
                      {truncate(entry.title, 32)}
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span
                        className="text-text-dim"
                        style={{ fontFamily: 'var(--font-mono)', fontSize: '10px' }}
                      >
                        {entry.source}
                      </span>
                      <span
                        className="text-text-dim"
                        style={{ fontFamily: 'var(--font-mono)', fontSize: '10px' }}
                      >
                        {relativeTime(entry.timestamp)}
                      </span>
                    </div>
                  </div>

                  {/* Quality badge */}
                  <span
                    className="shrink-0 mt-0.5 px-1"
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '9px',
                      fontWeight: 600,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      color: QUALITY_COLORS[entry.content_quality] ?? '#475569',
                      border: `1px solid ${(QUALITY_COLORS[entry.content_quality] ?? '#475569') + '44'}`,
                      borderRadius: '2px',
                      lineHeight: '14px',
                    }}
                  >
                    {entry.content_quality}
                  </span>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>

      {/* ── PIPELINE HEALTH ── */}
      <div className="border-t border-surface-border">
        <div className="panel-header">
          <div className="panel-title">
            <div className="panel-indicator" style={{ backgroundColor: lagColor(lagSeconds), boxShadow: `0 0 6px ${lagColor(lagSeconds)}66` }} />
            <span>Pipeline Health</span>
          </div>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              fontWeight: 600,
              color: lagColor(lagSeconds),
            }}
          >
            Lag: {lagSeconds}s
          </span>
        </div>

        <div className="px-2 py-1.5 space-y-0.5">
          {stages.length === 0 ? (
            <div className="text-xxs text-text-dim text-center py-2">No pipeline data</div>
          ) : (
            stages.map((stage) => (
              <div
                key={stage.name}
                className="flex items-center gap-1.5 py-0.5"
                style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}
              >
                <div className={statusDotClass(stage.status)} />
                <span className="text-text-secondary flex-1 min-w-0 truncate">{stage.name}</span>
                <span className="text-text-dim shrink-0" title="Queue size">
                  {stage.queue_size}q
                </span>
                <span className="shrink-0" style={{ color: '#00D4FF' }} title="Throughput">
                  {stage.throughput_per_minute.toFixed(1)}/m
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── SOURCE STATUS (collapsible) ── */}
      <div className="border-t border-surface-border">
        <button
          onClick={() => setSourceExpanded(prev => !prev)}
          className="panel-header w-full cursor-pointer hover:bg-base-500/20 transition-colors"
          style={{ background: 'none', border: 'none', textAlign: 'left' }}
        >
          <div className="panel-title">
            <span
              className="text-text-dim shrink-0 transition-transform duration-200"
              style={{
                display: 'inline-block',
                fontSize: '10px',
                transform: sourceExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
              }}
            >
              &#9654;
            </span>
            <span>Source Status</span>
          </div>
          <span className="data-readout">
            <span style={{ color: '#00CC66' }}>{sourceStatus.active}</span>
            {' active'}
            {sourceStatus.degraded > 0 && (
              <>
                {' \u00B7 '}
                <span style={{ color: '#FFB833' }}>{sourceStatus.degraded}</span>
                {' degraded'}
              </>
            )}
            {sourceStatus.dormant > 0 && (
              <>
                {' \u00B7 '}
                <span style={{ color: '#FF4444' }}>{sourceStatus.dormant}</span>
                {' dormant'}
              </>
            )}
          </span>
        </button>

        <AnimatePresence initial={false}>
          {sourceExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              style={{ overflow: 'hidden' }}
            >
              <div className="px-2 pb-2 space-y-2">
                {/* Group by status */}
                {(['active', 'degraded', 'dormant'] as const).map((statusGroup) => {
                  const items = sourceStatus.entries.filter(e => e.status === statusGroup)
                  if (items.length === 0) return null
                  return (
                    <div key={statusGroup}>
                      <div
                        className="text-text-dim font-display uppercase tracking-wider mb-0.5"
                        style={{ fontSize: '9px', fontWeight: 600 }}
                      >
                        {statusGroup}
                      </div>
                      {items.map((item) => (
                        <div
                          key={item.name}
                          className="flex items-center gap-1.5 py-0.5"
                          style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}
                        >
                          <div
                            className={
                              item.status === 'active'  ? 'status-dot healthy' :
                              item.status === 'degraded' ? 'status-dot degraded' :
                              'status-dot offline'
                            }
                          />
                          <span
                            className="text-text-secondary"
                            style={{ borderLeft: `2px solid ${CATEGORY_COLORS[item.category]}`, paddingLeft: '6px' }}
                          >
                            {item.name}
                          </span>
                        </div>
                      ))}
                    </div>
                  )
                })}

                {sourceStatus.entries.length === 0 && (
                  <div className="text-xxs text-text-dim text-center py-2">No source data available</div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
