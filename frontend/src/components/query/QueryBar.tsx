// ============================================
// QueryBar — Command-line styled natural language query input
// ============================================

import { useState, useRef, useCallback, useEffect } from 'react'
import { useStore } from '../../store'
import { peripheryApi } from '../../api'
import type { EntityNode } from '../../api'

// --------------- Autocomplete suggestion type ---------------

interface Suggestion {
  canonical_id: string
  name: string
  entity_type: string
  confidence: number
}

// --------------- Component ---------------

export function QueryBar() {
  const {
    currentQuery,
    setCurrentQuery,
    setQueryResult,
    isQuerying,
    setIsQuerying,
    queryHistory,
    addQueryToHistory,
    setQueryPanelExpanded,
    setHighlightedEntityIds,
    snapshot,
  } = useStore()

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Local UI state
  const [historyIndex, setHistoryIndex] = useState(-1)
  const [showHistory, setShowHistory] = useState(false)
  const [parsedIntent, setParsedIntent] = useState<Record<string, unknown> | null>(null)
  const [intentExpanded, setIntentExpanded] = useState(false)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [queryError, setQueryError] = useState<string | null>(null)

  // ---- Auto-grow textarea ----
  const autoGrow = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [])

  useEffect(() => {
    autoGrow()
  }, [currentQuery, autoGrow])

  // ---- Autocomplete from snapshot entities ----
  useEffect(() => {
    if (!currentQuery.trim() || !snapshot) {
      setSuggestions([])
      setShowSuggestions(false)
      return
    }

    const lower = currentQuery.toLowerCase()
    const words = lower.split(/\s+/)
    const lastWord = words[words.length - 1]
    if (!lastWord || lastWord.length < 2) {
      setSuggestions([])
      setShowSuggestions(false)
      return
    }

    const matches: Suggestion[] = []
    for (const entity of snapshot.entities) {
      if (matches.length >= 8) break
      const nameL = entity.name.toLowerCase()
      // prefix or fuzzy substring match
      if (nameL.startsWith(lastWord) || nameL.includes(lastWord)) {
        matches.push({
          canonical_id: entity.canonical_id,
          name: entity.name,
          entity_type: entity.entity_type,
          confidence: entity.confidence,
        })
      }
    }

    setSuggestions(matches)
    setShowSuggestions(matches.length > 0)
    setSelectedSuggestion(-1)
  }, [currentQuery, snapshot])

  // ---- Submit handler ----
  const handleSubmit = useCallback(async () => {
    const text = currentQuery.trim()
    if (!text || isQuerying) return

    setIsQuerying(true)
    setParsedIntent(null)
    setShowSuggestions(false)
    setQueryError(null)

    try {
      const result = await peripheryApi.query(text)
      setQueryResult(result)
      addQueryToHistory({
        query_id: result.query_id,
        query_text: text,
        timestamp: new Date().toISOString(),
        confidence: result.confidence,
      })
      setQueryPanelExpanded(true)
      setHighlightedEntityIds(
        new Set(result.entities.map((e) => e.canonical_id)),
      )
      setParsedIntent(result.parsed_intent as unknown as Record<string, unknown>)
    } catch (err) {
      console.error('[QueryBar] query failed:', err)
      setQueryError(err instanceof Error ? err.message : 'Query failed. Please try again.')
    } finally {
      setIsQuerying(false)
      setHistoryIndex(-1)
    }
  }, [
    currentQuery,
    isQuerying,
    setIsQuerying,
    setQueryResult,
    addQueryToHistory,
    setQueryPanelExpanded,
    setHighlightedEntityIds,
  ])

  // ---- Keyboard handling ----
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Autocomplete navigation
    if (showSuggestions && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedSuggestion((prev) =>
          prev < suggestions.length - 1 ? prev + 1 : 0,
        )
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedSuggestion((prev) =>
          prev > 0 ? prev - 1 : suggestions.length - 1,
        )
        return
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && selectedSuggestion >= 0)) {
        e.preventDefault()
        applySuggestion(suggestions[selectedSuggestion >= 0 ? selectedSuggestion : 0])
        return
      }
      if (e.key === 'Escape') {
        setShowSuggestions(false)
        return
      }
    }

    // Submit on Enter (not Shift+Enter)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
      return
    }

    // History navigation (only when single-line and no suggestions)
    if (!showSuggestions) {
      if (e.key === 'ArrowUp' && !e.shiftKey) {
        const lines = currentQuery.split('\n')
        if (lines.length <= 1) {
          e.preventDefault()
          navigateHistory(-1)
          return
        }
      }
      if (e.key === 'ArrowDown' && !e.shiftKey) {
        const lines = currentQuery.split('\n')
        if (lines.length <= 1) {
          e.preventDefault()
          navigateHistory(1)
          return
        }
      }
    }
  }

  const navigateHistory = (direction: number) => {
    if (queryHistory.length === 0) return
    const newIndex = historyIndex + direction
    if (direction < 0) {
      // Going back in history (up)
      if (newIndex < 0) {
        setHistoryIndex(0)
        setCurrentQuery(queryHistory[0].query_text)
      } else if (newIndex < queryHistory.length) {
        setHistoryIndex(newIndex)
        setCurrentQuery(queryHistory[newIndex].query_text)
      }
    } else {
      // Going forward in history (down)
      if (newIndex >= queryHistory.length) {
        setHistoryIndex(-1)
        setCurrentQuery('')
      } else {
        setHistoryIndex(newIndex)
        setCurrentQuery(queryHistory[newIndex].query_text)
      }
    }
  }

  const applySuggestion = (suggestion: Suggestion) => {
    const words = currentQuery.split(/\s+/)
    words[words.length - 1] = suggestion.name
    setCurrentQuery(words.join(' ') + ' ')
    setShowSuggestions(false)
    textareaRef.current?.focus()
  }

  const selectHistoryItem = (queryText: string) => {
    setCurrentQuery(queryText)
    setShowHistory(false)
    textareaRef.current?.focus()
  }

  // ---- Confidence color ----
  const confColor = (c: number) =>
    c >= 0.7 ? 'var(--confidence-high)' : c >= 0.4 ? 'var(--confidence-medium)' : 'var(--confidence-low)'

  return (
    <div className="query-bar-root" style={{ position: 'relative' }}>
      {/* Main input row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 8,
          background: '#0f1520',
          borderLeft: '2px solid var(--accent-cyan)',
          padding: '8px 12px',
          borderRadius: 'var(--border-radius)',
          position: 'relative',
        }}
      >
        {/* Prompt prefix */}
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            color: 'var(--accent-cyan)',
            fontSize: 14,
            lineHeight: '22px',
            userSelect: 'none',
            flexShrink: 0,
          }}
        >
          &gt;_
        </span>

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={currentQuery}
          onChange={(e) => setCurrentQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe what you're looking for..."
          disabled={isQuerying}
          rows={1}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            resize: 'none',
            fontFamily: 'var(--font-mono)',
            fontSize: 14,
            lineHeight: '22px',
            color: 'var(--text-primary)',
            caretColor: 'var(--accent-cyan)',
            overflow: 'hidden',
          }}
          className="query-bar-input"
        />

        {/* History dropdown toggle */}
        <button
          type="button"
          onClick={() => setShowHistory(!showHistory)}
          title="Query history"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-dim)',
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
            padding: '2px 4px',
            lineHeight: '22px',
            flexShrink: 0,
          }}
        >
          {showHistory ? '\u25B2' : '\u25BC'}
        </button>
      </div>

      {/* Scan line loading animation */}
      {isQuerying && (
        <div className="scan-line" style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: 2,
          overflow: 'hidden',
        }}>
          <div
            style={{
              width: '30%',
              height: '100%',
              background: 'linear-gradient(90deg, transparent, var(--accent-cyan), transparent)',
              animation: 'scan-line-move 1.5s ease-in-out infinite',
            }}
          />
        </div>
      )}

      {/* Query error display */}
      {queryError && !isQuerying && (
        <div
          style={{
            padding: '4px 12px 4px 28px',
            background: '#0a0e17',
            borderLeft: '2px solid var(--accent-red, #FF4444)',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
            color: 'var(--accent-red, #FF4444)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span style={{ flex: 1 }}>{queryError}</span>
          <button
            type="button"
            onClick={() => setQueryError(null)}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-dim)',
              fontFamily: 'var(--font-mono)',
              fontSize: 14,
              padding: '0 2px',
            }}
          >
            &times;
          </button>
        </div>
      )}

      {/* Parsed intent display */}
      {parsedIntent && !isQuerying && (
        <div
          style={{
            padding: '4px 12px 4px 28px',
            background: '#0a0e17',
            borderLeft: '2px solid var(--border-subtle)',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
          }}
        >
          <button
            type="button"
            onClick={() => setIntentExpanded(!intentExpanded)}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-dim)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              padding: 0,
            }}
          >
            <span style={{ color: 'var(--text-secondary)' }}>Interpreted as: </span>
            <span style={{ color: 'var(--accent-cyan)' }}>
              {(parsedIntent as { intent_type?: string }).intent_type ?? 'unknown'}
            </span>
            <span style={{ marginLeft: 6 }}>{intentExpanded ? '\u25B4' : '\u25BE'}</span>
          </button>
          {intentExpanded && (
            <pre
              style={{
                margin: '4px 0 0',
                fontSize: 10,
                color: 'var(--text-dim)',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.4,
              }}
            >
              {JSON.stringify(parsedIntent, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* Autocomplete suggestions dropdown */}
      {showSuggestions && suggestions.length > 0 && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            zIndex: 60,
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderTop: 'none',
            borderRadius: '0 0 var(--border-radius) var(--border-radius)',
            maxHeight: 200,
            overflowY: 'auto',
          }}
        >
          {suggestions.map((s, i) => (
            <button
              key={s.canonical_id}
              type="button"
              onClick={() => applySuggestion(s)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                width: '100%',
                padding: '6px 12px 6px 28px',
                background: i === selectedSuggestion ? 'var(--bg-tertiary)' : 'transparent',
                border: 'none',
                cursor: 'pointer',
                textAlign: 'left',
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
              }}
            >
              <span style={{ color: 'var(--text-primary)', flex: 1 }}>{s.name}</span>
              <span
                style={{
                  color: 'var(--text-dim)',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                {s.entity_type}
              </span>
              <span
                style={{
                  color: confColor(s.confidence),
                  fontSize: 10,
                  fontFamily: 'var(--font-mono)',
                  minWidth: 32,
                  textAlign: 'right',
                }}
              >
                {(s.confidence * 100).toFixed(0)}%
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Full history dropdown */}
      {showHistory && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            zIndex: 60,
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderTop: 'none',
            borderRadius: '0 0 var(--border-radius) var(--border-radius)',
            maxHeight: 240,
            overflowY: 'auto',
          }}
        >
          {queryHistory.length === 0 ? (
            <div
              style={{
                padding: '12px 16px',
                color: 'var(--text-dim)',
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                textAlign: 'center',
              }}
            >
              No query history
            </div>
          ) : (
            queryHistory.map((entry) => (
              <button
                key={entry.query_id}
                type="button"
                onClick={() => selectHistoryItem(entry.query_text)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  padding: '6px 12px',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: '1px solid var(--border-subtle)',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <span
                  style={{
                    flex: 1,
                    color: 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {entry.query_text}
                </span>
                <span
                  style={{
                    color: confColor(entry.confidence),
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    flexShrink: 0,
                  }}
                >
                  {(entry.confidence * 100).toFixed(0)}%
                </span>
                <span
                  style={{
                    color: 'var(--text-dim)',
                    fontSize: 10,
                    flexShrink: 0,
                  }}
                >
                  {new Date(entry.timestamp).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              </button>
            ))
          )}
        </div>
      )}

      {/* Inline styles for animations */}
      <style>{`
        .query-bar-input::placeholder {
          color: var(--text-dim);
          opacity: 0.6;
        }
        .query-bar-input:focus {
          caret-color: var(--accent-cyan);
        }
        @keyframes scan-line-move {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(400%); }
        }
      `}</style>
    </div>
  )
}
