// ============================================
// DetailPanel — Context-sensitive detail view
// Right-side panel showing entity, cluster,
// relationship, or anomaly details
// ============================================

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { useStore } from '../../store'
import { peripheryApi } from '../../api'
import { ConfidenceBadge, ConfidenceBar, Sparkline } from '../shared'
import type {
  EntityDetail,
  ClusterDetail,
  Relationship,
  Anomaly,
  ConfidenceFactor,
  EntityRelationship,
  ClusterTimelineEvent,
  RelationalGradient,
  SourceDocument,
} from '../../api'

// --- Calibrating Animation (loading state) ---

function CalibratingSpinner() {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <motion.div
        className="relative"
        style={{ width: 40, height: 40 }}
      >
        <motion.div
          className="absolute inset-0 border border-accent-cyan/40 rounded-full"
          animate={{ rotate: 360 }}
          transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          className="absolute inset-1 border border-accent-cyan/20 rounded-full"
          animate={{ rotate: -360 }}
          transition={{ duration: 5, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          className="absolute inset-0 flex items-center justify-center"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <div
            className="rounded-full bg-accent-cyan/60"
            style={{ width: 4, height: 4 }}
          />
        </motion.div>
      </motion.div>
      <span className="text-xxs text-text-dim font-display uppercase tracking-widest">
        Calibrating
      </span>
    </div>
  )
}

// --- Error state ---

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="p-3 m-2 border border-accent-red/30 bg-accent-red/5" style={{ borderRadius: 2 }}>
      <div className="text-xxs text-accent-red font-display uppercase tracking-wider mb-1">
        Signal Lost
      </div>
      <div className="text-xxs text-text-dim font-mono break-all">{message}</div>
    </div>
  )
}

// --- Section Label ---

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1 mt-3 first:mt-0">
      {children}
    </div>
  )
}

// --- Expandable section ---

function Expandable({ label, children, defaultOpen = false }: {
  label: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full text-left text-xxs text-text-dim font-display uppercase tracking-wider hover:text-text-secondary transition-colors"
      >
        <span className="font-mono text-accent-cyan/50" style={{ fontSize: 9 }}>
          {open ? '\u25BC' : '\u25B6'}
        </span>
        {label}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="pt-1">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// --- Confidence Factor Breakdown ---

function ConfidenceFactors({ factors }: { factors: ConfidenceFactor[] }) {
  return (
    <div className="space-y-1.5">
      {factors.map((f) => (
        <div key={f.name}>
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-xxs text-text-secondary truncate" style={{ maxWidth: '60%' }}>
              {f.name}
            </span>
            <span className="text-xxs font-mono text-text-dim">
              {(f.score * 100).toFixed(0)}% <span className="text-text-dim/50">w{(f.weight * 100).toFixed(0)}</span>
            </span>
          </div>
          <ConfidenceBar confidence={f.score} />
          {f.description && (
            <div className="text-xxs text-text-dim mt-0.5 leading-tight" style={{ fontSize: 10 }}>
              {f.description}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// --- Status color mapping for clusters ---

const clusterStatusColors: Record<string, string> = {
  forming: '#FFB833',
  stable: '#00CC66',
  growing: '#00D4FF',
  shrinking: '#FF4444',
}

// --- Temporal context badge ---

function TemporalBadge({ context }: { context: string }) {
  const colors: Record<string, string> = {
    current: '#00CC66',
    historical: '#94A3B8',
    speculative: '#FFB833',
  }
  const color = colors[context] || '#475569'
  return (
    <span
      className="inline-block text-xxs font-mono px-1 py-0.5"
      style={{
        fontSize: 10,
        color,
        border: `1px solid ${color}33`,
        borderRadius: 2,
      }}
    >
      {context}
    </span>
  )
}

// --- Extraction tier badge ---

function TierBadge({ tier }: { tier: string }) {
  const labels: Record<string, string> = {
    co_occurrence: 'CO-OCC',
    dependency: 'DEP',
    llm: 'LLM',
  }
  return (
    <span
      className="inline-block text-xxs font-mono px-1 py-0.5 text-text-dim border border-surface-border"
      style={{ fontSize: 10, borderRadius: 2 }}
    >
      {labels[tier] || tier.toUpperCase()}
    </span>
  )
}

// --- Quality badge for source documents ---

function QualityBadge({ quality }: { quality: string }) {
  const colors: Record<string, string> = {
    full: '#00CC66',
    summary: '#FFB833',
    metadata: '#475569',
  }
  const color = colors[quality] || '#475569'
  return (
    <span
      className="inline-block text-xxs font-mono px-1"
      style={{ fontSize: 10, color, border: `1px solid ${color}33`, borderRadius: 2 }}
    >
      {quality}
    </span>
  )
}

// =============================================
// ENTITY VIEW
// =============================================

function EntityView({ id }: { id: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['entity-detail', id],
    queryFn: () => peripheryApi.getEntity(id),
    staleTime: 30_000,
  })

  if (isLoading) return <CalibratingSpinner />
  if (error) return <ErrorBlock message={(error as Error).message} />
  if (!data) return null

  const entity: EntityDetail = data

  // Group relationships by predicate
  const relGroups = useMemo(() => {
    const groups: Record<string, EntityRelationship[]> = {}
    entity.relationships.forEach((r) => {
      if (!groups[r.predicate]) groups[r.predicate] = []
      groups[r.predicate].push(r)
    })
    return groups
  }, [entity.relationships])

  return (
    <>
      {/* Header */}
      <div className="mb-3">
        <div className="font-mono text-xs text-accent-cyan truncate mb-1">
          {entity.name}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="text-xxs text-text-dim font-display uppercase tracking-wider px-1 py-0.5 border border-surface-border"
            style={{ borderRadius: 2 }}
          >
            {entity.entity_type}
          </span>
          <ConfidenceBadge confidence={entity.confidence} showLabel />
        </div>
      </div>

      {/* Confidence explanation */}
      {entity.confidence_explanation && (
        <Expandable label={`Confidence Factors (${entity.confidence_explanation.factors.length})`}>
          <ConfidenceFactors factors={entity.confidence_explanation.factors} />
        </Expandable>
      )}

      {/* Aliases */}
      {entity.aliases.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Aliases</SectionLabel>
          <div className="text-xxs text-text-secondary font-mono leading-relaxed">
            {entity.aliases.join(', ')}
          </div>
        </div>
      )}

      {/* Cluster memberships */}
      {entity.cluster_memberships.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Cluster Memberships</SectionLabel>
          <div className="space-y-0.5">
            {entity.cluster_memberships.map((cm) => (
              <div
                key={cm.cluster_id}
                className="flex items-center gap-2 py-0.5 px-1 hover:bg-base-500/20 transition-colors"
                style={{ borderRadius: 2 }}
              >
                <ConfidenceBadge confidence={cm.confidence} size="sm" />
                <span className="text-xxs text-text-secondary truncate flex-1">{cm.label}</span>
                <span className="text-xxs font-mono text-text-dim">{cm.role}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Relationships grouped by predicate */}
      {Object.keys(relGroups).length > 0 && (
        <div className="mb-3">
          <SectionLabel>Relationships</SectionLabel>
          {Object.entries(relGroups).map(([predicate, rels]) => (
            <Expandable key={predicate} label={`${predicate} (${rels.length})`}>
              <div className="space-y-1.5">
                {rels.map((r) => (
                  <div
                    key={r.relationship_id}
                    className="p-1.5 bg-base-800/50 border border-surface-border/30"
                    style={{ borderRadius: 2 }}
                  >
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <span className="text-xxs text-text-secondary truncate flex-1">
                        {r.direction === 'outgoing' ? '\u2192' : '\u2190'} {r.other_entity_name}
                      </span>
                      <ConfidenceBadge confidence={r.confidence} size="sm" />
                    </div>
                    <div className="flex items-center gap-1 mb-0.5">
                      <TemporalBadge context={r.temporal_context} />
                    </div>
                    {r.evidence_sentence && (
                      <div className="text-xxs text-text-dim italic leading-tight mt-0.5" style={{ fontSize: 10 }}>
                        &ldquo;{r.evidence_sentence}&rdquo;
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Expandable>
          ))}
        </div>
      )}

      {/* Temporal history sparkline */}
      {entity.temporal_history.length >= 2 && (
        <div className="mb-3">
          <SectionLabel>Mention Frequency</SectionLabel>
          <div className="p-2 bg-base-800/50 border border-surface-border/30" style={{ borderRadius: 2 }}>
            <Sparkline
              data={entity.temporal_history.map((d) => d.count)}
              width={300}
              height={32}
              color="#00D4FF"
            />
            <div className="flex justify-between mt-0.5">
              <span className="text-xxs text-text-dim font-mono" style={{ fontSize: 9 }}>
                {entity.temporal_history[0]?.date}
              </span>
              <span className="text-xxs text-text-dim font-mono" style={{ fontSize: 9 }}>
                {entity.temporal_history[entity.temporal_history.length - 1]?.date}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Source documents */}
      {entity.source_documents.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Source Documents ({entity.source_documents.length})</SectionLabel>
          <div className="space-y-1">
            {entity.source_documents.map((doc: SourceDocument) => (
              <div
                key={doc.document_id}
                className="p-1.5 bg-base-800/50 border border-surface-border/30"
                style={{ borderRadius: 2 }}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-xxs text-text-secondary truncate flex-1">{doc.title}</span>
                  <QualityBadge quality={doc.content_quality} />
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xxs font-mono text-text-dim" style={{ fontSize: 10 }}>
                    {doc.source}
                  </span>
                  <span className="text-xxs font-mono text-text-dim" style={{ fontSize: 10 }}>
                    {doc.date}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

// =============================================
// CLUSTER VIEW
// =============================================

function ClusterView({ id }: { id: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['cluster-detail', id],
    queryFn: () => peripheryApi.getCluster(id),
    staleTime: 30_000,
  })

  if (isLoading) return <CalibratingSpinner />
  if (error) return <ErrorBlock message={(error as Error).message} />
  if (!data) return null

  const cluster: ClusterDetail = data
  const statusColor = clusterStatusColors[cluster.status] || '#475569'

  return (
    <>
      {/* Header */}
      <div className="mb-3">
        <div className="font-mono text-xs text-accent-cyan truncate mb-1">
          {cluster.label}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="text-xxs font-display uppercase tracking-wider px-1 py-0.5 border"
            style={{ borderRadius: 2, color: statusColor, borderColor: `${statusColor}44` }}
          >
            {cluster.status}
          </span>
          <ConfidenceBadge confidence={cluster.confidence} showLabel />
          <span className="text-xxs font-mono text-text-dim">{cluster.member_count} members</span>
        </div>
      </div>

      {/* Confidence explanation */}
      {cluster.confidence_explanation && (
        <Expandable label={`Confidence Factors (${cluster.confidence_explanation.factors.length})`}>
          <ConfidenceFactors factors={cluster.confidence_explanation.factors} />
        </Expandable>
      )}

      {/* Key entities */}
      {cluster.key_entities.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Key Entities</SectionLabel>
          <div className="space-y-0.5">
            {cluster.key_entities.map((e) => (
              <div
                key={e.canonical_id}
                className="flex items-center gap-2 py-0.5 px-1 hover:bg-base-500/20 transition-colors"
                style={{ borderRadius: 2 }}
              >
                <span className="text-xxs text-text-secondary truncate flex-1">{e.name}</span>
                <ConfidenceBadge confidence={e.confidence} size="sm" />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Internal relationships */}
      {cluster.internal_relationships.length > 0 && (
        <Expandable label={`Internal Relationships (${cluster.internal_relationships.length})`}>
          <div className="space-y-1">
            {cluster.internal_relationships.map((r, i) => (
              <div
                key={i}
                className="p-1.5 bg-base-800/50 border border-surface-border/30"
                style={{ borderRadius: 2 }}
              >
                <div className="text-xxs text-text-secondary">
                  {r.subject_name} <span className="text-accent-cyan/60">{r.predicate}</span> {r.object_name}
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <ConfidenceBadge confidence={r.confidence} size="sm" />
                  {r.evidence_snippet && (
                    <span className="text-xxs text-text-dim italic truncate" style={{ fontSize: 10 }}>
                      {r.evidence_snippet}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Expandable>
      )}

      {/* External connections (relational gradients) */}
      {cluster.external_connections.length > 0 && (
        <Expandable label={`External Connections (${cluster.external_connections.length})`}>
          <div className="space-y-1">
            {cluster.external_connections.map((g: RelationalGradient, i) => (
              <div
                key={i}
                className="flex items-center gap-2 py-0.5 px-1"
                style={{ borderRadius: 2 }}
              >
                <span className="text-xxs text-text-secondary truncate flex-1">
                  {g.target_cluster_id === id ? g.source_cluster_id : g.target_cluster_id}
                </span>
                <span className="text-xxs font-mono text-text-dim">
                  {g.relationship_count} rels
                </span>
                <div style={{ width: 60 }}>
                  <ConfidenceBar confidence={g.score} />
                </div>
              </div>
            ))}
          </div>
        </Expandable>
      )}

      {/* Trajectory sparkline */}
      {cluster.trajectory && cluster.trajectory.snapshots.length >= 2 && (
        <div className="mb-3">
          <SectionLabel>Trajectory</SectionLabel>
          <div className="p-2 bg-base-800/50 border border-surface-border/30" style={{ borderRadius: 2 }}>
            <div className="text-xxs text-text-dim mb-1 font-mono">
              {cluster.trajectory.pattern} &middot; v={cluster.trajectory.velocity.toFixed(2)}
            </div>
            <Sparkline
              data={cluster.trajectory.snapshots.map((s) =>
                s.position.reduce((a, b) => a + Math.abs(b), 0)
              )}
              width={300}
              height={32}
              color={statusColor}
            />
          </div>
        </div>
      )}

      {/* Timeline */}
      {cluster.timeline.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Cluster Timeline</SectionLabel>
          <div className="space-y-0.5 pl-2 border-l border-surface-border">
            {cluster.timeline.map((evt: ClusterTimelineEvent, i) => (
              <div key={i} className="py-0.5">
                <div className="flex items-center gap-1.5">
                  <span className="text-xxs font-mono text-text-dim" style={{ fontSize: 10 }}>
                    {evt.timestamp}
                  </span>
                  <span className="text-xxs font-display uppercase text-text-dim tracking-wider">
                    {evt.event_type}
                  </span>
                </div>
                <div className="text-xxs text-text-secondary leading-tight">{evt.description}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

// =============================================
// RELATIONSHIP VIEW
// =============================================

function RelationshipView({ id }: { id: string }) {
  const snapshot = useStore((s) => s.snapshot)

  const rel: Relationship | undefined = useMemo(() => {
    return snapshot?.relationships.find((r) => r.id === id)
  }, [snapshot, id])

  const subjectName = useMemo(() => {
    if (!snapshot || !rel) return rel?.subject_id ?? id
    const ent = snapshot.entities.find((e) => e.canonical_id === rel.subject_id)
    return ent?.name ?? rel.subject_id
  }, [snapshot, rel, id])

  const objectName = useMemo(() => {
    if (!snapshot || !rel) return rel?.object_id ?? ''
    const ent = snapshot.entities.find((e) => e.canonical_id === rel.object_id)
    return ent?.name ?? rel.object_id
  }, [snapshot, rel])

  if (!rel) {
    return <ErrorBlock message={`Relationship ${id} not found in current snapshot`} />
  }

  const evidenceSlice = rel.evidence_sentences.slice(0, 5)

  return (
    <>
      {/* Header */}
      <div className="mb-3">
        <div className="text-xxs text-text-secondary leading-tight mb-1">
          <span className="text-accent-cyan">{subjectName}</span>
          {' '}<span className="text-text-dim">&rarr;</span>{' '}
          <span className="font-display uppercase tracking-wider text-text-dim">{rel.predicate}</span>
          {' '}<span className="text-text-dim">&rarr;</span>{' '}
          <span className="text-accent-cyan">{objectName}</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <ConfidenceBadge confidence={rel.confidence} showLabel />
          <TemporalBadge context={rel.temporal_context} />
          <TierBadge tier={rel.extraction_tier} />
        </div>
      </div>

      {/* Cross-document consistency */}
      <div className="mb-3">
        <SectionLabel>Cross-Document Consistency</SectionLabel>
        <div className="flex items-center gap-2">
          <span className="text-xxs font-mono text-text-secondary">
            {rel.source_count} source{rel.source_count !== 1 ? 's' : ''}
          </span>
          <div style={{ width: 80 }}>
            <ConfidenceBar confidence={Math.min(1, rel.source_count / 5)} />
          </div>
        </div>
        <div className="text-xxs text-text-dim mt-0.5 font-mono" style={{ fontSize: 10 }}>
          First seen: {rel.first_seen} &middot; Last seen: {rel.last_seen}
        </div>
      </div>

      {/* Evidence sentences */}
      {evidenceSlice.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Evidence ({rel.evidence_sentences.length})</SectionLabel>
          <div className="space-y-1">
            {evidenceSlice.map((sentence, i) => (
              <div
                key={i}
                className="p-1.5 bg-base-800/50 border border-surface-border/30 text-xxs text-text-secondary italic leading-tight"
                style={{ borderRadius: 2, fontSize: 11 }}
              >
                &ldquo;{sentence}&rdquo;
              </div>
            ))}
            {rel.evidence_sentences.length > 5 && (
              <div className="text-xxs text-text-dim font-mono">
                +{rel.evidence_sentences.length - 5} more
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

// =============================================
// ANOMALY VIEW
// =============================================

function AnomalyView({ id }: { id: string }) {
  const snapshot = useStore((s) => s.snapshot)

  const anomaly: Anomaly | undefined = useMemo(() => {
    return snapshot?.anomalies.find((a) => a.anomaly_id === id)
  }, [snapshot, id])

  if (!anomaly) {
    return <ErrorBlock message={`Anomaly ${id} not found in current snapshot`} />
  }

  // Resolve related entity names
  const relatedEntities = useMemo(() => {
    if (!snapshot) return []
    return anomaly.related_entity_ids.map((eid) => {
      const ent = snapshot.entities.find((e) => e.canonical_id === eid)
      return { id: eid, name: ent?.name ?? eid }
    })
  }, [snapshot, anomaly.related_entity_ids])

  // Nearest cluster label
  const nearestClusterLabel = useMemo(() => {
    if (!snapshot || !anomaly.nearest_cluster_id) return null
    const cl = snapshot.clusters.find((c) => c.cluster_id === anomaly.nearest_cluster_id)
    return cl?.label ?? anomaly.nearest_cluster_id
  }, [snapshot, anomaly.nearest_cluster_id])

  const scoreColor = anomaly.anomaly_score >= 0.7
    ? '#FF4444'
    : anomaly.anomaly_score >= 0.4
      ? '#FFB833'
      : '#94A3B8'

  return (
    <>
      {/* Header */}
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1">
          <span
            className="text-xxs font-display uppercase tracking-wider px-1 py-0.5 border"
            style={{ borderRadius: 2, color: scoreColor, borderColor: `${scoreColor}44` }}
          >
            {anomaly.anomaly_type}
          </span>
          <span className="font-mono text-xs" style={{ color: scoreColor }}>
            {(anomaly.anomaly_score * 100).toFixed(0)}%
          </span>
        </div>
        <div className="text-xxs text-text-secondary leading-tight">{anomaly.description}</div>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xxs text-text-dim font-display uppercase tracking-wider">
            Source Credibility
          </span>
          <ConfidenceBadge confidence={anomaly.source_credibility} size="sm" />
        </div>
      </div>

      {/* Why anomalous */}
      {anomaly.flagging_spaces.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Flagging Embedding Spaces</SectionLabel>
          <div className="flex flex-wrap gap-1">
            {anomaly.flagging_spaces.map((space) => (
              <span
                key={space}
                className="text-xxs font-mono px-1 py-0.5 bg-accent-red/10 text-accent-red/80 border border-accent-red/20"
                style={{ borderRadius: 2, fontSize: 10 }}
              >
                {space}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Nearest cluster */}
      {anomaly.nearest_cluster_id && (
        <div className="mb-3">
          <SectionLabel>Nearest Cluster</SectionLabel>
          <div className="p-1.5 bg-base-800/50 border border-surface-border/30" style={{ borderRadius: 2 }}>
            <div className="text-xxs text-text-secondary">{nearestClusterLabel}</div>
            {anomaly.nearest_cluster_distance != null && (
              <div className="text-xxs font-mono text-text-dim mt-0.5" style={{ fontSize: 10 }}>
                distance: {anomaly.nearest_cluster_distance.toFixed(4)}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Related entities */}
      {relatedEntities.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Related Entities ({relatedEntities.length})</SectionLabel>
          <div className="space-y-0.5">
            {relatedEntities.map((e) => (
              <div
                key={e.id}
                className="flex items-center gap-2 py-0.5 px-1 hover:bg-base-500/20 transition-colors"
                style={{ borderRadius: 2 }}
              >
                <span className="text-accent-cyan/50" style={{ fontSize: 8 }}>{'\u25CB'}</span>
                <span className="text-xxs text-text-secondary truncate">{e.name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="mb-3">
        <SectionLabel>Actions</SectionLabel>
        <div className="flex gap-1.5">
          <button
            className="text-xxs font-display uppercase tracking-wider px-2 py-1 border border-accent-red/40 text-accent-red hover:bg-accent-red/10 transition-colors"
            style={{ borderRadius: 2 }}
          >
            Flag for Review
          </button>
          <button
            className="text-xxs font-display uppercase tracking-wider px-2 py-1 border border-surface-border text-text-dim hover:bg-base-500/20 transition-colors"
            style={{ borderRadius: 2 }}
          >
            Dismiss
          </button>
          <button
            className="text-xxs font-display uppercase tracking-wider px-2 py-1 border border-accent-amber/40 text-accent-amber hover:bg-accent-amber/10 transition-colors"
            style={{ borderRadius: 2 }}
          >
            Monitor
          </button>
        </div>
      </div>
    </>
  )
}

// =============================================
// DETAIL PANEL (main export)
// =============================================

export function DetailPanel() {
  const selectedElement = useStore((s) => s.selectedElement)
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  const isOpen = selectedElement !== null

  const typeLabels: Record<string, string> = {
    entity: 'Entity Intel',
    cluster: 'Cluster Analysis',
    relationship: 'Relationship Intel',
    anomaly: 'Anomaly Report',
  }

  return (
    <AnimatePresence mode="wait">
      {isOpen && (
        <motion.div
          key="detail-panel"
          className="panel flex flex-col h-full overflow-hidden"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 360, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.25, ease: 'easeInOut' }}
          style={{
            minWidth: 0,
            maxWidth: 360,
            fontSize: 13,
            fontFamily: 'var(--font-display), sans-serif',
          }}
        >
          {/* Panel header */}
          <div className="panel-header flex-shrink-0">
            <div className="panel-title">
              <div
                className="panel-indicator"
                style={{
                  backgroundColor: 'var(--accent-cyan)',
                  boxShadow: '0 0 6px var(--accent-cyan-dim)',
                }}
              />
              <span>{typeLabels[selectedElement.type] || 'Detail'}</span>
            </div>
            <button
              onClick={() => setSelectedElement(null)}
              className="text-xxs text-text-dim hover:text-text-secondary transition-colors font-mono"
              title="Close panel"
            >
              &times;
            </button>
          </div>

          {/* Scrollable body */}
          <div
            className="panel-body flex-1 overflow-y-auto"
            style={{
              fontSize: 13,
              fontFamily: 'var(--font-display), sans-serif',
            }}
          >
            {selectedElement.type === 'entity' && <EntityView id={selectedElement.id} />}
            {selectedElement.type === 'cluster' && <ClusterView id={selectedElement.id} />}
            {selectedElement.type === 'relationship' && <RelationshipView id={selectedElement.id} />}
            {selectedElement.type === 'anomaly' && <AnomalyView id={selectedElement.id} />}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
