// ============================================
// QueryBar — natural language query input
// ============================================

import React, { useState, useRef, useEffect } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'

export const QueryBar: React.FC = () => {
  const currentQuery = useStore((s) => s.currentQuery)
  const setCurrentQuery = useStore((s) => s.setCurrentQuery)
  const setQueryResult = useStore((s) => s.setQueryResult)
  const isQuerying = useStore((s) => s.isQuerying)
  const setIsQuerying = useStore((s) => s.setIsQuerying)
  const addQueryToHistory = useStore((s) => s.addQueryToHistory)
  const queryPanelExpanded = useStore((s) => s.queryPanelExpanded)
  const setQueryPanelExpanded = useStore((s) => s.setQueryPanelExpanded)
  const queryHistory = useStore((s) => s.queryHistory)

  const inputRef = useRef<HTMLInputElement>(null)
  const [showHistory, setShowHistory] = useState(false)

  // Keyboard shortcut: Ctrl+K to focus
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const q = currentQuery.trim()
    if (!q || isQuerying) return

    setIsQuerying(true)
    setQueryPanelExpanded(true)
    setQueryResult(null)

    try {
      const result = await peripheryApi.query(q)
      setQueryResult(result)
      addQueryToHistory({
        query_id: result.query_id,
        query_text: q,
        timestamp: new Date().toISOString(),
        confidence: result.confidence,
      })
    } catch (err: any) {
      // Show error as a pseudo-result
      setQueryResult(null)
    } finally {
      setIsQuerying(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSubmit()
    if (e.key === 'Escape') {
      setShowHistory(false)
      inputRef.current?.blur()
    }
    if (e.key === 'ArrowUp' && !currentQuery) {
      setShowHistory(true)
    }
  }

  return (
    <div className="relative">
      <form onSubmit={handleSubmit} className="flex items-center gap-2 px-3 py-2 bg-base-800 border-t border-surface-border">
        {/* Prompt indicator */}
        <span className="text-accent-cyan font-mono text-xs shrink-0">
          {isQuerying ? (
            <span className="animate-pulse">⟳</span>
          ) : (
            '▸'
          )}
        </span>

        <input
          ref={inputRef}
          type="text"
          value={currentQuery}
          onChange={(e) => setCurrentQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => queryHistory.length > 0 && setShowHistory(true)}
          onBlur={() => setTimeout(() => setShowHistory(false), 200)}
          placeholder="Query the ontology… (Ctrl+K)"
          className="flex-1 bg-transparent border-none outline-none text-xs font-mono text-text-primary placeholder:text-text-dim"
          disabled={isQuerying}
        />

        {/* Toggle results */}
        {useStore.getState().queryResult && (
          <button
            type="button"
            className="btn-secondary !py-0.5 !px-1.5 text-xxs"
            onClick={() => setQueryPanelExpanded(!queryPanelExpanded)}
          >
            {queryPanelExpanded ? '▾ HIDE' : '▴ SHOW'}
          </button>
        )}

        <button
          type="submit"
          className="btn-primary !py-1 !px-2"
          disabled={isQuerying || !currentQuery.trim()}
        >
          {isQuerying ? 'ANALYZING…' : 'QUERY'}
        </button>
      </form>

      {/* History dropdown */}
      {showHistory && queryHistory.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 bg-base-800 border border-surface-border border-b-0 max-h-40 overflow-y-auto z-10">
          {queryHistory.slice(0, 10).map((h, i) => (
            <button
              key={`${h.query_id}-${i}`}
              className="w-full text-left px-3 py-1.5 text-xxs font-mono text-text-secondary hover:bg-base-500/30 hover:text-text-primary transition-colors"
              onMouseDown={() => {
                setCurrentQuery(h.query_text)
                setShowHistory(false)
              }}
            >
              {h.query_text}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default QueryBar
