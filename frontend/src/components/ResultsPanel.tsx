import React from 'react'
import type { QueryResponse } from '../api'

interface Props {
  result: QueryResponse
}

export function ResultsPanel({ result }: Props) {
  const confidenceColor =
    result.confidence > 0.7 ? '#4caf50' : result.confidence > 0.4 ? '#ff9800' : '#f44336'

  const confidenceLabel =
    result.confidence > 0.7
      ? 'High confidence'
      : result.confidence > 0.4
        ? 'Medium confidence'
        : 'Low confidence — emerging pattern'

  return (
    <div style={styles.container}>
      {/* Confidence indicator */}
      <div style={styles.confidenceBar}>
        <div
          style={{
            ...styles.confidenceFill,
            width: `${result.confidence * 100}%`,
            backgroundColor: confidenceColor,
          }}
        />
        <span style={styles.confidenceLabel}>{confidenceLabel} ({(result.confidence * 100).toFixed(0)}%)</span>
      </div>

      {/* Answer */}
      <div style={styles.answer}>
        <p style={{ whiteSpace: 'pre-wrap', margin: 0, lineHeight: 1.6 }}>{result.answer}</p>
      </div>

      {/* Sources — legibility gradient */}
      {result.sources.length > 0 && (
        <div style={styles.sources}>
          <h3 style={styles.sourcesTitle}>Sources ({result.sources.length})</h3>
          {result.sources.map((source, i) => {
            const opacity = Math.max(0.3, source.score)
            return (
              <div
                key={source.document.id}
                style={{
                  ...styles.sourceCard,
                  opacity,
                  borderLeftColor: source.score > 0.7 ? '#4caf50' : source.score > 0.4 ? '#ff9800' : '#444',
                }}
              >
                <div style={styles.sourceHeader}>
                  <span style={styles.sourceIndex}>#{i + 1}</span>
                  <span style={styles.sourceScore}>{(source.score * 100).toFixed(1)}% relevance</span>
                </div>
                <p style={styles.sourceContent}>{source.document.content}</p>
              </div>
            )
          })}
        </div>
      )}

      {/* Graph context */}
      {result.graph_context && result.graph_context.nodes.length > 0 && (
        <div style={styles.graphContext}>
          <h3 style={styles.sourcesTitle}>Graph Context</h3>
          <p style={{ color: '#888', fontSize: 13 }}>
            {result.graph_context.cluster_count} clusters, {result.graph_context.document_count} connected documents,{' '}
            {result.graph_context.edges.length} relationships
          </p>
        </div>
      )}
    </div>
  )
}

const styles = {
  container: {
    backgroundColor: '#12121f',
    borderRadius: 12,
    padding: 24,
    border: '1px solid #1a1a2e',
  } as React.CSSProperties,
  confidenceBar: {
    position: 'relative' as const,
    height: 6,
    backgroundColor: '#1a1a2e',
    borderRadius: 3,
    marginBottom: 20,
    overflow: 'hidden',
  } as React.CSSProperties,
  confidenceFill: {
    position: 'absolute' as const,
    top: 0,
    left: 0,
    height: '100%',
    borderRadius: 3,
    transition: 'width 0.5s ease',
  } as React.CSSProperties,
  confidenceLabel: {
    position: 'absolute' as const,
    right: 0,
    top: 10,
    fontSize: 11,
    color: '#888',
  } as React.CSSProperties,
  answer: {
    fontSize: 15,
    color: '#d0d0d0',
    marginBottom: 24,
    marginTop: 16,
  } as React.CSSProperties,
  sources: {
    borderTop: '1px solid #1a1a2e',
    paddingTop: 16,
  } as React.CSSProperties,
  sourcesTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#888',
    textTransform: 'uppercase' as const,
    letterSpacing: 1,
    marginBottom: 12,
  } as React.CSSProperties,
  sourceCard: {
    padding: '12px 16px',
    marginBottom: 8,
    borderLeft: '3px solid #444',
    backgroundColor: '#0d0d1a',
    borderRadius: '0 4px 4px 0',
    transition: 'opacity 0.3s',
  } as React.CSSProperties,
  sourceHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: 4,
  } as React.CSSProperties,
  sourceIndex: {
    fontSize: 11,
    fontWeight: 600,
    color: '#666',
  } as React.CSSProperties,
  sourceScore: {
    fontSize: 11,
    color: '#888',
  } as React.CSSProperties,
  sourceContent: {
    fontSize: 13,
    color: '#aaa',
    margin: 0,
    lineHeight: 1.5,
  } as React.CSSProperties,
  graphContext: {
    borderTop: '1px solid #1a1a2e',
    paddingTop: 16,
    marginTop: 16,
  } as React.CSSProperties,
}
