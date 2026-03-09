// ============================================
// QueryResults — Expandable panel for analytical query results
// ============================================

import { useState, useMemo, useCallback, Fragment } from 'react'
import { useStore } from '../../store'
import type {
  AnalyticalQueryResponse,
  EntityResult,
  RelationshipResult,
  ClusterResult,
  TrajectoryResult,
  AnomalyResult,
} from '../../api'

// --------------- Constants ---------------

type ResultTab = 'entities' | 'relationships' | 'clusters' | 'trajectories' | 'anomalies'

const TABS: { key: ResultTab; label: string }[] = [
  { key: 'entities', label: 'Entities' },
  { key: 'relationships', label: 'Relationships' },
  { key: 'clusters', label: 'Clusters' },
  { key: 'trajectories', label: 'Trajectories' },
  { key: 'anomalies', label: 'Anomalies' },
]

// --------------- Helpers ---------------

function confColor(c: number): string {
  if (c >= 0.7) return 'var(--confidence-high)'
  if (c >= 0.4) return 'var(--confidence-medium)'
  return 'var(--confidence-low)'
}

function confLevel(c: number): 'high' | 'medium' | 'low' {
  if (c >= 0.7) return 'high'
  if (c >= 0.4) return 'medium'
  return 'low'
}

/** Highlight IC-style confidence language in narrative text */
function renderNarrative(text: string): JSX.Element {
  // Patterns in order of specificity
  const patterns: { regex: RegExp; color: string }[] = [
    { regex: /We assess with high confidence/g, color: 'var(--accent-cyan)' },
    { regex: /Reporting suggests/g, color: 'var(--accent-amber)' },
    { regex: /There are indications/g, color: 'var(--accent-amber-dim)' },
  ]

  // Build segments with highlight ranges
  interface Segment { text: string; color?: string }
  const segments: Segment[] = []
  let remaining = text

  // Simple sequential scan approach
  while (remaining.length > 0) {
    let earliest: { index: number; length: number; color: string } | null = null

    for (const p of patterns) {
      p.regex.lastIndex = 0
      const match = p.regex.exec(remaining)
      if (match && (earliest === null || match.index < earliest.index)) {
        earliest = { index: match.index, length: match[0].length, color: p.color }
      }
    }

    if (!earliest) {
      segments.push({ text: remaining })
      break
    }

    if (earliest.index > 0) {
      segments.push({ text: remaining.slice(0, earliest.index) })
    }
    segments.push({
      text: remaining.slice(earliest.index, earliest.index + earliest.length),
      color: earliest.color,
    })
    remaining = remaining.slice(earliest.index + earliest.length)
  }

  return (
    <>
      {segments.map((seg, i) =>
        seg.color ? (
          <span key={i} style={{ color: seg.color, fontWeight: 600 }}>
            {seg.text}
          </span>
        ) : (
          <Fragment key={i}>{seg.text}</Fragment>
        ),
      )}
    </>
  )
}

// --------------- Confidence Bar (tiny) ---------------

function ConfBar({ value }: { value: number }) {
  return (
    <div
      style={{
        width: 48,
        height: 4,
        background: 'var(--bg-primary)',
        borderRadius: 1,
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      <div
        style={{
          width: `${Math.round(value * 100)}%`,
          height: '100%',
          background: confColor(value),
          borderRadius: 1,
        }}
      />
    </div>
  )
}

// --------------- Sort helper ---------------

function sortByConfidence<T extends { confidence?: number; anomaly_score?: number }>(
  items: T[],
): T[] {
  return [...items].sort(
    (a, b) => (b.confidence ?? b.anomaly_score ?? 0) - (a.confidence ?? a.anomaly_score ?? 0),
  )
}

// --------------- Component ---------------

export function QueryResults() {
  const {
    queryResult,
    queryPanelExpanded,
    setCurrentQuery,
    setSelectedElement,
  } = useStore()

  const [activeTab, setActiveTab] = useState<ResultTab>('entities')

  // ---- Follow-up click handler ----
  const handleFollowup = useCallback(
    (text: string) => {
      setCurrentQuery(text)
    },
    [setCurrentQuery],
  )

  // ---- Sorted data ----
  const sortedEntities = useMemo(
    () => (queryResult ? sortByConfidence(queryResult.entities) : []),
    [queryResult],
  )
  const sortedRelationships = useMemo(
    () => (queryResult ? sortByConfidence(queryResult.relationships) : []),
    [queryResult],
  )
  const sortedClusters = useMemo(
    () => (queryResult ? sortByConfidence(queryResult.clusters) : []),
    [queryResult],
  )
  const sortedAnomalies = useMemo(
    () => (queryResult ? sortByConfidence(queryResult.anomalies) : []),
    [queryResult],
  )

  if (!queryResult || !queryPanelExpanded) return null

  return (
    <div
      style={{
        background: 'var(--bg-panel)',
        borderTop: '1px solid var(--border-subtle)',
        maxHeight: '40vh',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Two-pane layout */}
      <div
        style={{
          display: 'flex',
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        {/* ===== LEFT: Analytical Narrative ===== */}
        <div
          style={{
            flex: '1 1 50%',
            overflowY: 'auto',
            padding: 'var(--panel-padding)',
            borderRight: '1px solid var(--border-subtle)',
          }}
        >
          {/* Narrative */}
          <p
            style={{
              margin: '0 0 12px',
              fontSize: 14,
              fontFamily: 'var(--font-display)',
              lineHeight: 1.55,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {renderNarrative(queryResult.narrative)}
          </p>

          {/* Key Findings */}
          {queryResult.key_findings.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 10,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-dim)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  marginBottom: 6,
                }}
              >
                Key Findings
              </div>
              {queryResult.key_findings.map((finding, i) => {
                const level = confLevel(finding.confidence)
                const borderColor =
                  level === 'high'
                    ? 'var(--accent-cyan)'
                    : level === 'medium'
                      ? 'var(--accent-amber)'
                      : 'var(--confidence-low)'

                return (
                  <div
                    key={i}
                    className="finding-card"
                    style={{
                      borderLeft: `2px solid ${borderColor}`,
                      padding: '4px 8px',
                      marginBottom: 4,
                      fontSize: 12,
                      fontFamily: 'var(--font-display)',
                      lineHeight: 1.4,
                      color: 'var(--text-secondary)',
                    }}
                  >
                    {finding.text}
                  </div>
                )
              })}
            </div>
          )}

          {/* Gaps */}
          {queryResult.gaps.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 10,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-dim)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  marginBottom: 6,
                }}
              >
                Intelligence Gaps
              </div>
              {queryResult.gaps.map((gap, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 11,
                    color: 'var(--text-dim)',
                    lineHeight: 1.4,
                    marginBottom: 2,
                    paddingLeft: 8,
                    fontFamily: 'var(--font-display)',
                  }}
                >
                  &bull; {gap}
                </div>
              ))}
            </div>
          )}

          {/* Suggested follow-ups */}
          {queryResult.suggested_followups.length > 0 && (
            <div>
              <div
                style={{
                  fontSize: 10,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-dim)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  marginBottom: 6,
                }}
              >
                Follow-up Queries
              </div>
              {queryResult.suggested_followups.map((followup, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => handleFollowup(followup)}
                  style={{
                    display: 'block',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--accent-cyan)',
                    padding: '2px 0',
                    textAlign: 'left',
                    textDecoration: 'underline',
                    textDecorationColor: 'var(--accent-cyan-dim)',
                    textUnderlineOffset: 2,
                  }}
                >
                  &gt; {followup}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ===== RIGHT: Structured Results ===== */}
        <div
          style={{
            flex: '1 1 50%',
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            overflow: 'hidden',
          }}
        >
          {/* Tab bar */}
          <div
            className="tab-bar"
            style={{
              display: 'flex',
              borderBottom: '1px solid var(--border-subtle)',
              flexShrink: 0,
            }}
          >
            {TABS.map((tab) => {
              const count = getTabCount(queryResult, tab.key)
              const isActive = activeTab === tab.key
              return (
                <button
                  key={tab.key}
                  type="button"
                  className="tab-item"
                  onClick={() => setActiveTab(tab.key)}
                  style={{
                    flex: 1,
                    padding: '6px 4px',
                    background: isActive ? 'var(--bg-tertiary)' : 'transparent',
                    border: 'none',
                    borderBottom: isActive ? '1px solid var(--accent-cyan)' : '1px solid transparent',
                    cursor: 'pointer',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: isActive ? 'var(--accent-cyan)' : 'var(--text-dim)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {tab.label}
                  {count > 0 && (
                    <span
                      style={{
                        marginLeft: 4,
                        color: isActive ? 'var(--text-secondary)' : 'var(--text-dim)',
                      }}
                    >
                      {count}
                    </span>
                  )}
                </button>
              )
            })}
          </div>

          {/* Tab content */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--panel-padding)' }}>
            {activeTab === 'entities' && (
              <ResultTable
                headers={['Name', 'Type', 'Conf', 'Sources', 'Temporal']}
                rows={sortedEntities}
                renderRow={(e: EntityResult) => (
                  <tr
                    key={e.canonical_id}
                    onClick={() => setSelectedElement({ type: 'entity', id: e.canonical_id })}
                    style={{ cursor: 'pointer' }}
                    className="result-row"
                  >
                    <td style={cellStyle}>{e.name}</td>
                    <td style={{ ...cellStyle, color: 'var(--text-dim)', textTransform: 'uppercase' as const, fontSize: 10 }}>
                      {e.entity_type}
                    </td>
                    <td style={cellStyle}>
                      <ConfBar value={e.confidence} />
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'center' as const }}>{e.source_count}</td>
                    <td style={{ ...cellStyle, color: 'var(--text-dim)', fontSize: 10 }}>
                      {e.temporal_context}
                    </td>
                  </tr>
                )}
              />
            )}

            {activeTab === 'relationships' && (
              <ResultTable
                headers={['Subject', 'Predicate', 'Object', 'Conf', 'Evidence']}
                rows={sortedRelationships}
                renderRow={(r: RelationshipResult, i: number) => (
                  <tr key={i} className="result-row" style={{ cursor: 'default' }}>
                    <td style={cellStyle}>{r.subject_name}</td>
                    <td style={{ ...cellStyle, color: 'var(--accent-amber)', fontSize: 10 }}>
                      {r.predicate}
                    </td>
                    <td style={cellStyle}>{r.object_name}</td>
                    <td style={cellStyle}>
                      <ConfBar value={r.confidence} />
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        color: 'var(--text-dim)',
                        fontSize: 10,
                        maxWidth: 140,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={r.evidence_snippet}
                    >
                      {r.evidence_snippet}
                    </td>
                  </tr>
                )}
              />
            )}

            {activeTab === 'clusters' && (
              <ResultTable
                headers={['Label', 'Conf', 'Members', 'Relevance']}
                rows={sortedClusters}
                renderRow={(c: ClusterResult) => (
                  <tr
                    key={c.cluster_id}
                    onClick={() => setSelectedElement({ type: 'cluster', id: c.cluster_id })}
                    style={{ cursor: 'pointer' }}
                    className="result-row"
                  >
                    <td style={cellStyle}>{c.label}</td>
                    <td style={cellStyle}>
                      <ConfBar value={c.confidence} />
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'center' as const }}>{c.member_count}</td>
                    <td style={{ ...cellStyle, textAlign: 'center' as const }}>
                      {(c.relevance_score * 100).toFixed(0)}%
                    </td>
                  </tr>
                )}
              />
            )}

            {activeTab === 'trajectories' && (
              <ResultTable
                headers={['Cluster', 'Pattern', 'Velocity']}
                rows={queryResult.trajectories}
                renderRow={(t: TrajectoryResult) => (
                  <tr key={t.trajectory_id} className="result-row" style={{ cursor: 'default' }}>
                    <td style={cellStyle}>{t.cluster_label}</td>
                    <td style={{ ...cellStyle, color: 'var(--text-dim)' }}>{t.pattern}</td>
                    <td style={{ ...cellStyle, textAlign: 'center' as const }}>
                      {t.velocity.toFixed(2)}
                    </td>
                  </tr>
                )}
              />
            )}

            {activeTab === 'anomalies' && (
              <ResultTable
                headers={['Type', 'Score', 'Description']}
                rows={sortedAnomalies}
                renderRow={(a: AnomalyResult) => (
                  <tr
                    key={a.anomaly_id}
                    onClick={() => setSelectedElement({ type: 'anomaly', id: a.anomaly_id })}
                    style={{ cursor: 'pointer' }}
                    className="result-row"
                  >
                    <td style={{ ...cellStyle, textTransform: 'uppercase' as const, fontSize: 10 }}>
                      {a.anomaly_type}
                    </td>
                    <td style={cellStyle}>
                      <ConfBar value={a.anomaly_score} />
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        color: 'var(--text-dim)',
                        maxWidth: 220,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={a.description}
                    >
                      {a.description}
                    </td>
                  </tr>
                )}
              />
            )}
          </div>
        </div>
      </div>

      {/* Inline styles for hover */}
      <style>{`
        .result-row:hover td {
          background: var(--bg-tertiary);
        }
      `}</style>
    </div>
  )
}

// --------------- Shared cell style ---------------

const cellStyle: React.CSSProperties = {
  padding: '4px 6px',
  fontFamily: 'var(--font-mono)',
  fontSize: 12,
  lineHeight: 1.3,
  color: 'var(--text-secondary)',
  borderBottom: '1px solid var(--border-subtle)',
  verticalAlign: 'middle',
}

// --------------- Generic result table ---------------

function ResultTable<T>({
  headers,
  rows,
  renderRow,
}: {
  headers: string[]
  rows: T[]
  renderRow: (row: T, index: number) => JSX.Element
}) {
  if (rows.length === 0) {
    return (
      <div
        style={{
          padding: 16,
          color: 'var(--text-dim)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          textAlign: 'center',
        }}
      >
        No results
      </div>
    )
  }

  return (
    <table
      style={{
        width: '100%',
        borderCollapse: 'collapse',
        tableLayout: 'auto',
      }}
    >
      <thead>
        <tr>
          {headers.map((h) => (
            <th
              key={h}
              style={{
                padding: '4px 6px',
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: 'var(--text-dim)',
                textAlign: 'left',
                borderBottom: '1px solid var(--border-active)',
                whiteSpace: 'nowrap',
                position: 'sticky',
                top: 0,
                background: 'var(--bg-panel)',
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{rows.map((row, i) => renderRow(row, i))}</tbody>
    </table>
  )
}

// --------------- Tab count helper ---------------

function getTabCount(result: AnalyticalQueryResponse, tab: ResultTab): number {
  switch (tab) {
    case 'entities':
      return result.entities.length
    case 'relationships':
      return result.relationships.length
    case 'clusters':
      return result.clusters.length
    case 'trajectories':
      return result.trajectories.length
    case 'anomalies':
      return result.anomalies.length
  }
}
