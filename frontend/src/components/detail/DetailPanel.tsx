// ============================================
// DetailPanel — right-side detail view
// Shows entity, cluster, or document details using RecordViewer
// ============================================

import React, { useEffect, useState } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'
import type { EntityDetail, ClusterDetail } from '../../api/types'
import { RecordViewer } from '../records/RecordViewer'
import { ClassificationBadge } from '../records/ClassificationBadge'
import { ConfidenceBadge, EntityChip, Sparkline, DataSourcesFooter } from '../shared'

export const DetailPanel: React.FC = () => {
  const selectedElement = useStore((s) => s.selectedElement)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const detailPanelWidth = useStore((s) => s.detailPanelWidth)
  const setDetailPanelWidth = useStore((s) => s.setDetailPanelWidth)

  // Auto-open panel when something is selected
  useEffect(() => {
    if (selectedElement && detailPanelWidth === 0) {
      setDetailPanelWidth(360)
    }
  }, [selectedElement, detailPanelWidth, setDetailPanelWidth])

  if (!selectedElement || detailPanelWidth === 0) return null

  return (
    <aside
      className="panel flex flex-col shrink-0 h-full overflow-hidden"
      style={{ width: detailPanelWidth }}
    >
      <div className="panel-header">
        <div className="panel-title">
          <span className="panel-indicator" />
          DETAIL
        </div>
        <button
          onClick={() => {
            setSelectedElement(null)
            setDetailPanelWidth(0)
          }}
          className="text-text-dim hover:text-text-primary transition-colors text-xs"
        >
          ✕
        </button>
      </div>

      <div className="panel-body flex-1 overflow-y-auto">
        {selectedElement.type === 'entity' && (
          <EntityDetailView entityId={selectedElement.id} />
        )}
        {selectedElement.type === 'cluster' && (
          <ClusterDetailView clusterId={selectedElement.id} />
        )}
        {selectedElement.type === 'document' && (
          <DocumentDetailView
            documentId={selectedElement.id}
            data={selectedElement.data}
          />
        )}
        {selectedElement.type === 'anomaly' && (
          <AnomalyDetailView anomalyId={selectedElement.id} />
        )}
      </div>
    </aside>
  )
}

// ---- Entity Detail ----

const EntityDetailView: React.FC<{ entityId: string }> = ({ entityId }) => {
  const [detail, setDetail] = useState<EntityDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  useEffect(() => {
    setLoading(true)
    setError(null)
    peripheryApi.getEntity(entityId)
      .then(setDetail)
      .catch((err) => setError(err?.message || 'Failed to load entity'))
      .finally(() => setLoading(false))
  }, [entityId])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />
  if (!detail) return null

  const timelineCounts = detail.temporal_history.map((t) => t.count)

  return (
    <div className="space-y-3">
      {/* Header */}
      <div>
        <h3 className="text-sm text-text-bright font-medium">{detail.name}</h3>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xxs font-mono text-accent-cyan uppercase">{detail.entity_type}</span>
          <ConfidenceBadge confidence={detail.confidence} showLabel />
          <span className="text-xxs text-text-dim">{detail.source_count} sources</span>
        </div>
      </div>

      {/* Aliases */}
      {detail.aliases.length > 0 && (
        <div>
          <span className="data-readout">ALIASES</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {detail.aliases.map((a, i) => (
              <span key={i} className="text-xxs font-mono px-1.5 py-0.5 bg-base-500/30 rounded-sm text-text-secondary">
                {a}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Timeline sparkline */}
      {timelineCounts.length > 1 && (
        <div>
          <span className="data-readout">TEMPORAL ACTIVITY</span>
          <div className="mt-1">
            <Sparkline data={timelineCounts} width={280} height={24} />
          </div>
        </div>
      )}

      {/* Confidence explanation */}
      {detail.confidence_explanation?.factors?.length > 0 && (
        <div>
          <span className="data-readout">CONFIDENCE FACTORS</span>
          <div className="space-y-1 mt-1">
            {detail.confidence_explanation.factors.map((f, i) => (
              <div key={i} className="flex items-center justify-between text-xxs">
                <span className="text-text-secondary">{f.name}</span>
                <div className="flex items-center gap-2">
                  <div className="w-16 h-1.5 bg-base-400 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent-cyan rounded-full"
                      style={{ width: `${f.score * 100}%` }}
                    />
                  </div>
                  <span className="font-mono text-text-dim w-8 text-right">{(f.score * 100).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cluster memberships */}
      {detail.cluster_memberships.length > 0 && (
        <div>
          <span className="data-readout">CLUSTERS</span>
          <div className="space-y-1 mt-1">
            {detail.cluster_memberships.map((c) => (
              <button
                key={c.cluster_id}
                className="w-full text-left flex items-center gap-2 text-xxs px-2 py-1 hover:bg-base-500/30 rounded-sm"
                onClick={() => setSelectedElement({ type: 'cluster', id: c.cluster_id })}
              >
                <span className="text-text-primary">{c.label}</span>
                <span className="text-text-dim">{c.role}</span>
                <ConfidenceBadge confidence={c.confidence} className="ml-auto" />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Relationships */}
      {detail.relationships.length > 0 && (
        <div>
          <span className="data-readout">RELATIONSHIPS ({detail.relationships.length})</span>
          <div className="space-y-1 mt-1">
            {detail.relationships.slice(0, 10).map((r) => (
              <button
                key={r.relationship_id}
                className="w-full text-left text-xxs font-mono px-2 py-1 hover:bg-base-500/30 rounded-sm"
                onClick={() => setSelectedElement({ type: 'entity', id: r.other_entity_id })}
              >
                <span className={r.direction === 'outgoing' ? 'text-accent-cyan' : 'text-accent-amber'}>
                  {r.direction === 'outgoing' ? '→' : '←'}
                </span>{' '}
                <span className="text-text-dim">{r.predicate}</span>{' '}
                <span className="text-text-primary">{r.other_entity_name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Source documents */}
      <DataSourcesFooter
        sources={detail.source_documents.map((d) => d.source)}
        count={detail.source_count}
      />
    </div>
  )
}

// ---- Cluster Detail ----

const ClusterDetailView: React.FC<{ clusterId: string }> = ({ clusterId }) => {
  const [detail, setDetail] = useState<ClusterDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    peripheryApi.getCluster(clusterId)
      .then(setDetail)
      .catch((err) => setError(err?.message || 'Failed to load cluster'))
      .finally(() => setLoading(false))
  }, [clusterId])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />
  if (!detail) return null

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm text-text-bright font-medium">{detail.label}</h3>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xxs font-mono text-accent-amber uppercase">{detail.status}</span>
          <ConfidenceBadge confidence={detail.confidence} showLabel />
          <span className="text-xxs text-text-dim">{detail.member_count} members</span>
        </div>
      </div>

      {/* Key entities */}
      {detail.key_entities.length > 0 && (
        <div>
          <span className="data-readout">KEY ENTITIES</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {detail.key_entities.map((e) => (
              <EntityChip
                key={e.canonical_id}
                id={e.canonical_id}
                name={e.name}
                entityType={e.entity_type}
                confidence={e.confidence}
              />
            ))}
          </div>
        </div>
      )}

      {/* Timeline */}
      {detail.timeline.length > 0 && (
        <div>
          <span className="data-readout">TIMELINE</span>
          <div className="space-y-1 mt-1">
            {detail.timeline.slice(0, 8).map((ev, i) => (
              <div key={i} className="flex items-start gap-2 text-xxs">
                <span className="text-text-dim font-mono shrink-0">{ev.timestamp.slice(0, 10)}</span>
                <span className="text-accent-amber uppercase font-mono text-xxs">{ev.event_type}</span>
                <span className="text-text-secondary">{ev.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Internal relationships */}
      {detail.internal_relationships.length > 0 && (
        <div>
          <span className="data-readout">INTERNAL RELATIONSHIPS ({detail.internal_relationships.length})</span>
          <div className="space-y-1 mt-1">
            {detail.internal_relationships.slice(0, 5).map((r, i) => (
              <div key={i} className="flex items-center gap-1.5 text-xxs font-mono">
                <span className="text-text-primary">{r.subject_name}</span>
                <span className="text-accent-amber">→ {r.predicate} →</span>
                <span className="text-text-primary">{r.object_name}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---- Document Detail (using RecordViewer) ----

const DocumentDetailView: React.FC<{
  documentId: string
  data?: { id: string; content: string; metadata: Record<string, unknown>; created_at: string }
}> = ({ documentId, data }) => {
  if (!data) {
    return (
      <div className="text-center py-4">
        <span className="data-readout text-text-dim">Document ID: {documentId}</span>
      </div>
    )
  }

  return (
    <RecordViewer
      metadata={data.metadata}
      content={data.content}
      documentId={documentId}
      classification={data.metadata.data_classification as string}
      sourceType={data.metadata.source_type as string}
    />
  )
}

// ---- Anomaly Detail ----

const AnomalyDetailView: React.FC<{ anomalyId: string }> = ({ anomalyId }) => {
  const snapshot = useStore((s) => s.snapshot)
  const anomaly = snapshot?.anomalies.find((a) => a.anomaly_id === anomalyId)

  if (!anomaly) {
    return <ErrorState message={`Anomaly ${anomalyId} not found`} />
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm text-accent-red font-medium">⚠ {anomaly.anomaly_type}</h3>
        <div className="flex items-center gap-2 mt-1">
          <ConfidenceBadge confidence={anomaly.anomaly_score} showLabel />
          <span className="text-xxs text-text-dim">{anomaly.detected_at.slice(0, 10)}</span>
        </div>
      </div>

      <p className="text-xs text-text-secondary">{anomaly.description}</p>

      {anomaly.flagging_spaces.length > 0 && (
        <div>
          <span className="data-readout">FLAGGING SPACES</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {anomaly.flagging_spaces.map((s, i) => (
              <span key={i} className="text-xxs font-mono px-1.5 py-0.5 bg-red-900/10 border border-red-900/20 rounded-sm text-accent-red">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---- Shared States ----

const LoadingState: React.FC = () => (
  <div className="text-center py-8">
    <div className="calibrating w-16 mx-auto mb-2" />
    <span className="data-readout">LOADING…</span>
  </div>
)

const ErrorState: React.FC<{ message: string }> = ({ message }) => (
  <div className="text-center py-8">
    <p className="text-accent-red text-xs">{message}</p>
  </div>
)

export default DetailPanel
