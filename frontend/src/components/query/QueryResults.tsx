// ============================================
// QueryResults — query results display panel
// ============================================

import React from 'react'
import { useStore } from '../../store'
import { EntityChip, ConfidenceBadge } from '../shared'

export const QueryResults: React.FC = () => {
  const queryResult = useStore((s) => s.queryResult)
  const queryPanelExpanded = useStore((s) => s.queryPanelExpanded)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const setHighlightedEntityIds = useStore((s) => s.setHighlightedEntityIds)
  const isQuerying = useStore((s) => s.isQuerying)

  if (!queryPanelExpanded) return null

  if (isQuerying) {
    return (
      <div className="border-t border-surface-border bg-base-800 p-4">
        <div className="flex items-center gap-3">
          <div className="calibrating w-24" />
          <span className="data-readout">ANALYZING QUERY…</span>
        </div>
      </div>
    )
  }

  if (!queryResult) return null

  const {
    narrative,
    key_findings,
    entities,
    relationships,
    clusters,
    anomalies,
    gaps,
    suggested_followups,
    confidence,
    processing_time_ms,
    parsed_intent,
  } = queryResult

  return (
    <div className="border-t border-surface-border bg-base-800 max-h-80 overflow-y-auto">
      {/* Summary header */}
      <div className="px-3 py-2 border-b border-surface-border flex items-center gap-3">
        <span className="data-readout">ANALYSIS COMPLETE</span>
        <ConfidenceBadge confidence={confidence} showLabel />
        <span className="data-readout text-xxs ml-auto">{processing_time_ms.toFixed(0)}ms</span>
        {parsed_intent?.intent_type && (
          <span className="text-xxs font-mono text-accent-amber">{parsed_intent.intent_type}</span>
        )}
      </div>

      <div className="p-3 space-y-3">
        {/* Narrative */}
        {narrative && (
          <div className="text-xs text-text-primary leading-relaxed">{narrative}</div>
        )}

        {/* Key Findings */}
        {key_findings.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">KEY FINDINGS</h4>
            <div className="space-y-1">
              {key_findings.map((f, i) => (
                <div
                  key={i}
                  className={`finding-card ${f.confidence >= 0.7 ? 'high' : f.confidence >= 0.4 ? 'medium' : 'low'}`}
                >
                  <p className="text-xs text-text-primary">{f.text}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Entities */}
        {entities.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">ENTITIES ({entities.length})</h4>
            <div className="flex flex-wrap gap-1">
              {entities.map((e) => (
                <EntityChip
                  key={e.canonical_id}
                  id={e.canonical_id}
                  name={e.name}
                  entityType={e.entity_type}
                  confidence={e.confidence}
                />
              ))}
            </div>
            <button
              className="btn-secondary !py-0.5 !px-1.5 text-xxs mt-1"
              onClick={() => {
                setHighlightedEntityIds(new Set(entities.map((e) => e.canonical_id)))
              }}
            >
              HIGHLIGHT IN GRAPH
            </button>
          </div>
        )}

        {/* Relationships */}
        {relationships.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">RELATIONSHIPS ({relationships.length})</h4>
            <div className="space-y-1">
              {relationships.slice(0, 5).map((r, i) => (
                <div key={i} className="flex items-center gap-1.5 text-xxs font-mono">
                  <span className="text-text-primary">{r.subject_name}</span>
                  <span className="text-accent-amber">→ {r.predicate} →</span>
                  <span className="text-text-primary">{r.object_name}</span>
                  <ConfidenceBadge confidence={r.confidence} className="ml-auto" />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Clusters */}
        {clusters.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">CLUSTERS ({clusters.length})</h4>
            <div className="space-y-1">
              {clusters.map((c) => (
                <button
                  key={c.cluster_id}
                  className="flex items-center gap-2 text-xxs w-full text-left px-2 py-1 hover:bg-base-500/30 rounded-sm"
                  onClick={() => setSelectedElement({ type: 'cluster', id: c.cluster_id })}
                >
                  <span className="text-text-primary font-medium">{c.label}</span>
                  <span className="text-text-dim">{c.member_count} members</span>
                  <ConfidenceBadge confidence={c.confidence} className="ml-auto" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Anomalies */}
        {anomalies.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5 text-accent-red">⚠ ANOMALIES ({anomalies.length})</h4>
            <div className="space-y-1">
              {anomalies.map((a) => (
                <div key={a.anomaly_id} className="text-xxs text-text-secondary px-2 py-1 bg-red-900/10 border border-red-900/20 rounded-sm">
                  <span className="text-accent-red font-mono">{a.anomaly_type}</span>: {a.description}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Gaps */}
        {gaps.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">GAPS & LIMITATIONS</h4>
            <ul className="space-y-0.5">
              {gaps.map((g, i) => (
                <li key={i} className="text-xxs text-text-dim pl-2 border-l border-surface-border">{g}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Follow-ups */}
        {suggested_followups.length > 0 && (
          <div>
            <h4 className="data-readout mb-1.5">SUGGESTED FOLLOW-UPS</h4>
            <div className="flex flex-wrap gap-1">
              {suggested_followups.map((f, i) => (
                <button
                  key={i}
                  className="btn-secondary !py-0.5 !px-1.5 text-xxs"
                  onClick={() => useStore.getState().setCurrentQuery(f)}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default QueryResults
