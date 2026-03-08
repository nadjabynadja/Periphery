import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { QueryResponse } from '../api'

interface Props {
  onSubmit: (question: string) => void
  isLoading: boolean
  result: QueryResponse | null
}

export function QueryInterface({ onSubmit, isLoading, result }: Props) {
  const [question, setQuestion] = useState('')
  const [showResults, setShowResults] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (question.trim() && !isLoading) {
      onSubmit(question.trim())
      setShowResults(true)
    }
  }

  const confidenceColor = result
    ? result.confidence > 0.7 ? '#00d4ff' : result.confidence > 0.4 ? '#d4a000' : '#ff3333'
    : '#4a5568'

  return (
    <div className="relative">
      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <span className="text-accent-cyan text-xxs font-mono shrink-0">&gt;_</span>
        <input
          ref={inputRef}
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onFocus={() => result && setShowResults(true)}
          placeholder="query the ontology..."
          className="command-input flex-1"
          disabled={isLoading}
          style={{ border: 'none', background: 'transparent', padding: '4px 0' }}
        />
        {isLoading && (
          <div className="flex items-center gap-1.5 shrink-0">
            <div className="calibrating" style={{ width: '40px' }} />
            <span className="text-xxs text-text-dim font-mono">PROCESSING</span>
          </div>
        )}
        {result && !isLoading && (
          <button
            type="button"
            onClick={() => setShowResults(!showResults)}
            className="shrink-0 flex items-center gap-1.5 px-2 py-0.5 text-xxs font-mono transition-colors"
            style={{
              color: confidenceColor,
              background: `${confidenceColor}11`,
              border: `1px solid ${confidenceColor}33`,
              borderRadius: '2px',
            }}
          >
            {(result.confidence * 100).toFixed(0)}%
            <span className="text-text-dim">{showResults ? '\u25B2' : '\u25BC'}</span>
          </button>
        )}
      </form>

      {/* Results dropdown */}
      <AnimatePresence>
        {showResults && result && !isLoading && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="absolute top-full left-0 right-0 mt-1 z-50"
          >
            <div className="bg-base-800 border border-surface-border" style={{ borderRadius: '2px', maxHeight: '400px', overflow: 'auto' }}>
              {/* Confidence bar */}
              <div className="px-3 pt-2 pb-1">
                <div className="h-0.5 bg-base-500 overflow-hidden" style={{ borderRadius: '1px' }}>
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: `${result.confidence * 100}%`,
                      backgroundColor: confidenceColor,
                      boxShadow: `0 0 6px ${confidenceColor}66`,
                    }}
                  />
                </div>
                <div className="flex justify-between mt-1">
                  <span className="text-xxs text-text-dim font-mono">
                    {result.confidence > 0.7 ? 'HIGH CONFIDENCE' :
                     result.confidence > 0.4 ? 'MEDIUM CONFIDENCE' : 'LOW CONFIDENCE — EMERGING PATTERN'}
                  </span>
                  <button
                    onClick={() => setShowResults(false)}
                    className="text-xxs text-text-dim hover:text-text-secondary"
                  >
                    CLOSE
                  </button>
                </div>
              </div>

              {/* Answer */}
              <div className="px-3 py-2 border-t border-surface-border">
                <p className="text-xs text-text-primary leading-relaxed m-0 whitespace-pre-wrap">
                  {result.answer}
                </p>
              </div>

              {/* Sources */}
              {result.sources.length > 0 && (
                <div className="px-3 py-2 border-t border-surface-border">
                  <div className="text-xxs text-text-dim font-display uppercase tracking-wider mb-1.5">
                    Sources ({result.sources.length})
                  </div>
                  {result.sources.slice(0, 5).map((source, i) => {
                    const opacity = Math.max(0.3, source.score)
                    return (
                      <div
                        key={source.document.id}
                        className="flex items-start gap-2 py-1 border-l-2 pl-2 mb-1"
                        style={{
                          opacity,
                          borderLeftColor: source.score > 0.7 ? '#00d4ff' : source.score > 0.4 ? '#d4a000' : '#4a5568',
                        }}
                      >
                        <span className="text-xxs text-text-dim font-mono shrink-0">{String(i + 1).padStart(2, '0')}</span>
                        <p className="text-xxs text-text-secondary m-0 leading-snug line-clamp-2">
                          {source.document.content}
                        </p>
                        <span className="text-xxs font-mono shrink-0" style={{ color: confidenceColor }}>
                          {(source.score * 100).toFixed(0)}%
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* Graph context */}
              {result.graph_context && result.graph_context.nodes.length > 0 && (
                <div className="px-3 py-2 border-t border-surface-border">
                  <span className="data-readout">
                    Graph: {result.graph_context.cluster_count} clusters, {result.graph_context.document_count} docs, {result.graph_context.edges.length} edges
                  </span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
