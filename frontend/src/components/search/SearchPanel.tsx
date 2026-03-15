import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useStore } from '../../store'
import { peripheryApi } from '../../api'
import type {
  DocumentSearchResult,
  EntitySearchResult,
  RelationshipSearchResult,
  DocumentSearchResponse,
  EntitySearchResponse,
  RelationshipSearchResponse,
  SuggestResponse,
  FacetsResponse,
} from '../../api/types'

type SearchTab = 'documents' | 'entities' | 'relationships'

function confidenceBorderColor(confidence: number): string {
  if (confidence >= 0.8) return 'var(--accent-cyan, #00d4ff)'
  if (confidence >= 0.6) return 'var(--accent-green, #00cc66)'
  if (confidence >= 0.4) return 'var(--accent-amber, #ffb833)'
  return 'var(--accent-red, #ff4444)'
}

function highlightText(text: string, query: string): JSX.Element {
  if (!query || query.length < 2) return <>{text}</>
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark
            key={i}
            style={{
              background: 'var(--accent-cyan, #00d4ff)',
              color: 'var(--bg-primary, #0a0e17)',
              opacity: 0.8,
              borderRadius: 1,
              padding: '0 1px',
            }}
          >
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  )
}

export function SearchPanel() {
  const searchPanelOpen = useStore((s) => s.searchPanelOpen)
  const setSearchPanelOpen = useStore((s) => s.setSearchPanelOpen)
  const searchQuery = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  const [activeTab, setActiveTab] = useState<SearchTab>('documents')
  const [inputValue, setInputValue] = useState(searchQuery)
  const [debouncedQuery, setDebouncedQuery] = useState(searchQuery)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [filtersExpanded, setFiltersExpanded] = useState(false)

  // Filters
  const [filterSourceFeed, setFilterSourceFeed] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [filterEntityType, setFilterEntityType] = useState('')
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')
  const [filterMinConfidence, setFilterMinConfidence] = useState(0)
  const [filterHasLocation, setFilterHasLocation] = useState<boolean | undefined>(undefined)
  const [filterPredicate, setFilterPredicate] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  // Pagination
  const [docResults, setDocResults] = useState<DocumentSearchResult[]>([])
  const [entityResults, setEntityResults] = useState<EntitySearchResult[]>([])
  const [relResults, setRelResults] = useState<RelationshipSearchResult[]>([])
  const [docTotal, setDocTotal] = useState(0)
  const [entityTotal, setEntityTotal] = useState(0)
  const [relTotal, setRelTotal] = useState(0)
  const [docOffset, setDocOffset] = useState(0)
  const [entityOffset, setEntityOffset] = useState(0)
  const [relOffset, setRelOffset] = useState(0)

  const inputRef = useRef<HTMLInputElement>(null)
  const suggestRef = useRef<HTMLDivElement>(null)

  // Debounce input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(inputValue)
      setSearchQuery(inputValue)
      // Reset pagination on new query
      setDocOffset(0)
      setEntityOffset(0)
      setRelOffset(0)
      setDocResults([])
      setEntityResults([])
      setRelResults([])
    }, 300)
    return () => clearTimeout(timer)
  }, [inputValue, setSearchQuery])

  // Focus input when panel opens
  useEffect(() => {
    if (searchPanelOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [searchPanelOpen])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && searchPanelOpen) {
        setSearchPanelOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [searchPanelOpen, setSearchPanelOpen])

  // Close suggestions on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (suggestRef.current && !suggestRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // --- Queries ---

  const { data: facets } = useQuery<FacetsResponse>({
    queryKey: ['searchFacets', debouncedQuery],
    queryFn: () => peripheryApi.searchFacets(debouncedQuery ? { q: debouncedQuery } : undefined),
    staleTime: 30_000,
  })

  const { data: suggestions } = useQuery<SuggestResponse>({
    queryKey: ['searchSuggest', inputValue],
    queryFn: () => peripheryApi.searchSuggest({ q: inputValue, limit: 8 }),
    enabled: inputValue.length >= 2 && showSuggestions,
    staleTime: 10_000,
  })

  // Document search
  const { isFetching: docFetching } = useQuery<DocumentSearchResponse>({
    queryKey: ['searchDocuments', debouncedQuery, filterSourceFeed, filterCategory, filterDateFrom, filterDateTo, filterStatus, docOffset],
    queryFn: async () => {
      const res = await peripheryApi.searchDocuments({
        q: debouncedQuery,
        source_feed: filterSourceFeed || undefined,
        category: filterCategory || undefined,
        date_from: filterDateFrom || undefined,
        date_to: filterDateTo || undefined,
        status: filterStatus || undefined,
        limit: 25,
        offset: docOffset,
      })
      if (docOffset === 0) {
        setDocResults(res.results)
      } else {
        setDocResults((prev) => [...prev, ...res.results])
      }
      setDocTotal(res.total_count)
      return res
    },
    enabled: debouncedQuery.length >= 2 && activeTab === 'documents',
    staleTime: 15_000,
  })

  // Entity search
  const { isFetching: entityFetching } = useQuery<EntitySearchResponse>({
    queryKey: ['searchEntities', debouncedQuery, filterEntityType, filterHasLocation, filterMinConfidence, entityOffset],
    queryFn: async () => {
      const res = await peripheryApi.searchEntities({
        q: debouncedQuery,
        entity_type: filterEntityType || undefined,
        has_location: filterHasLocation,
        min_confidence: filterMinConfidence > 0 ? filterMinConfidence : undefined,
        limit: 25,
        offset: entityOffset,
      })
      if (entityOffset === 0) {
        setEntityResults(res.results)
      } else {
        setEntityResults((prev) => [...prev, ...res.results])
      }
      setEntityTotal(res.total_count)
      return res
    },
    enabled: debouncedQuery.length >= 2 && activeTab === 'entities',
    staleTime: 15_000,
  })

  // Relationship search
  const { isFetching: relFetching } = useQuery<RelationshipSearchResponse>({
    queryKey: ['searchRelationships', debouncedQuery, filterPredicate, filterMinConfidence, relOffset],
    queryFn: async () => {
      const res = await peripheryApi.searchRelationships({
        q: debouncedQuery,
        predicate: filterPredicate || undefined,
        min_confidence: filterMinConfidence > 0 ? filterMinConfidence : undefined,
        limit: 25,
        offset: relOffset,
      })
      if (relOffset === 0) {
        setRelResults(res.results)
      } else {
        setRelResults((prev) => [...prev, ...res.results])
      }
      setRelTotal(res.total_count)
      return res
    },
    enabled: debouncedQuery.length >= 2 && activeTab === 'relationships',
    staleTime: 15_000,
  })

  const handleSuggestionClick = useCallback(
    (text: string) => {
      setInputValue(text)
      setShowSuggestions(false)
    },
    [],
  )

  const handleDocClick = useCallback(
    (id: string) => {
      setSelectedElement({ type: 'entity', id })
    },
    [setSelectedElement],
  )

  const isFetching = activeTab === 'documents' ? docFetching : activeTab === 'entities' ? entityFetching : relFetching
  const hasQuery = debouncedQuery.length >= 2

  if (!searchPanelOpen) return null

  return (
    <div
      className="panel flex flex-col h-full"
      style={{
        background: 'var(--bg-secondary, #0f1520)',
        borderRight: '1px solid var(--border-subtle, #1e293b)',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div className="panel-header flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="panel-indicator" />
          <span
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--text-secondary, #94a3b8)',
            }}
          >
            SEARCH
          </span>
          <span className="data-readout" style={{ color: 'var(--text-dim)', fontSize: 9 }}>
            Ctrl+K
          </span>
        </div>
        <button
          onClick={() => setSearchPanelOpen(false)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-dim, #475569)',
            cursor: 'pointer',
            fontSize: 14,
            padding: '0 4px',
            fontFamily: 'var(--font-mono)',
          }}
          title="Close search"
        >
          &times;
        </button>
      </div>

      {/* Search Input */}
      <div className="px-3 py-2" style={{ position: 'relative' }} ref={suggestRef}>
        <input
          ref={inputRef}
          type="text"
          className="command-input"
          placeholder="Search documents, entities..."
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value)
            setShowSuggestions(true)
          }}
          onFocus={() => inputValue.length >= 2 && setShowSuggestions(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') setShowSuggestions(false)
          }}
        />
        {/* Autocomplete dropdown */}
        {showSuggestions && suggestions && (suggestions.entities.length > 0 || suggestions.documents.length > 0) && (
          <div
            style={{
              position: 'absolute',
              top: '100%',
              left: 12,
              right: 12,
              zIndex: 100,
              background: 'var(--bg-tertiary, #141c2b)',
              border: '1px solid var(--border-subtle, #1e293b)',
              borderRadius: 2,
              maxHeight: 240,
              overflowY: 'auto',
            }}
          >
            {suggestions.entities.length > 0 && (
              <>
                <div
                  className="data-readout"
                  style={{ padding: '6px 8px 2px', color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.1em' }}
                >
                  ENTITIES
                </div>
                {suggestions.entities.map((e, i) => (
                  <button
                    key={`e-${i}`}
                    onClick={() => handleSuggestionClick(e.text)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      width: '100%',
                      padding: '4px 8px',
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      textAlign: 'left',
                    }}
                    className="hover:bg-base-700"
                  >
                    <span style={{ color: 'var(--accent-cyan)', fontSize: 9, minWidth: 40 }}>
                      {e.type}
                    </span>
                    <span>{e.text}</span>
                  </button>
                ))}
              </>
            )}
            {suggestions.documents.length > 0 && (
              <>
                <div
                  className="data-readout"
                  style={{ padding: '6px 8px 2px', color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.1em' }}
                >
                  DOCUMENTS
                </div>
                {suggestions.documents.map((d, i) => (
                  <button
                    key={`d-${i}`}
                    onClick={() => handleSuggestionClick(d.title)}
                    style={{
                      display: 'block',
                      width: '100%',
                      padding: '4px 8px',
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      textAlign: 'left',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    className="hover:bg-base-700"
                  >
                    {d.title}
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* Tab Selector */}
      <div className="flex items-center gap-1 px-3 pb-1">
        {(['documents', 'entities', 'relationships'] as SearchTab[]).map((tab) => {
          const count = tab === 'documents' ? docTotal : tab === 'entities' ? entityTotal : relTotal
          const isActive = activeTab === tab
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-2 py-0.5 text-xxs font-display font-semibold tracking-wider uppercase border transition-all ${
                isActive
                  ? 'text-accent-cyan border-accent-cyan/30 bg-accent-cyan/5'
                  : 'text-text-dim border-transparent hover:text-text-secondary'
              }`}
              style={{ borderRadius: 2 }}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
              {hasQuery && count > 0 ? ` (${count})` : ''}
            </button>
          )
        })}
      </div>

      {/* Filters */}
      <div className="px-3 py-1 border-t border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <button
          onClick={() => setFiltersExpanded(!filtersExpanded)}
          className="data-readout"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-dim)',
            fontSize: 9,
            letterSpacing: '0.1em',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 0',
            width: '100%',
          }}
        >
          <span style={{ display: 'inline-block', transform: filtersExpanded ? 'rotate(90deg)' : 'rotate(0)', transition: 'transform 0.15s' }}>
            &#9656;
          </span>
          FILTERS
        </button>
        {filtersExpanded && (
          <div className="flex flex-col gap-1.5 py-1.5" style={{ fontSize: 10 }}>
            {/* Source feed */}
            <div className="flex items-center gap-2">
              <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Source</label>
              <select
                value={filterSourceFeed}
                onChange={(e) => setFilterSourceFeed(e.target.value)}
                className="command-input"
                style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
              >
                <option value="">All Sources</option>
                {facets?.source_feeds.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name} ({f.count})
                  </option>
                ))}
              </select>
            </div>

            {/* Category */}
            {activeTab === 'documents' && (
              <>
                <div className="flex items-center gap-2">
                  <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Category</label>
                  <select
                    value={filterCategory}
                    onChange={(e) => setFilterCategory(e.target.value)}
                    className="command-input"
                    style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
                  >
                    <option value="">All Categories</option>
                    {facets?.categories.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name} ({c.count})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center gap-2">
                  <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Status</label>
                  <select
                    value={filterStatus}
                    onChange={(e) => setFilterStatus(e.target.value)}
                    className="command-input"
                    style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
                  >
                    <option value="">All Statuses</option>
                    {facets?.processing_statuses.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name} ({s.count})
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}

            {/* Entity type (entities tab) */}
            {activeTab === 'entities' && (
              <div className="flex items-center gap-2">
                <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Type</label>
                <select
                  value={filterEntityType}
                  onChange={(e) => setFilterEntityType(e.target.value)}
                  className="command-input"
                  style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
                >
                  <option value="">All Types</option>
                  {facets?.entity_types.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name} ({t.count})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Date range */}
            <div className="flex items-center gap-2">
              <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Date</label>
              <input
                type="date"
                value={filterDateFrom}
                onChange={(e) => setFilterDateFrom(e.target.value)}
                className="command-input"
                style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
              />
              <span style={{ color: 'var(--text-dim)' }}>&rarr;</span>
              <input
                type="date"
                value={filterDateTo}
                onChange={(e) => setFilterDateTo(e.target.value)}
                className="command-input"
                style={{ fontSize: 10, padding: '2px 4px', flex: 1 }}
              />
            </div>

            {/* Confidence slider */}
            <div className="flex items-center gap-2">
              <label className="data-readout" style={{ color: 'var(--text-dim)', minWidth: 50, fontSize: 9 }}>Conf.</label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={filterMinConfidence}
                onChange={(e) => setFilterMinConfidence(parseFloat(e.target.value))}
                style={{ flex: 1, accentColor: 'var(--accent-cyan)' }}
              />
              <span className="data-value" style={{ fontSize: 10, minWidth: 26 }}>
                {filterMinConfidence.toFixed(2)}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      <div className="panel-body flex-1" style={{ overflowY: 'auto' }}>
        {!hasQuery && (
          <div
            style={{
              padding: '24px 12px',
              textAlign: 'center',
              color: 'var(--text-dim)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              lineHeight: 1.6,
            }}
          >
            Search across all documents, entities, and relationships in the intelligence corpus
          </div>
        )}

        {hasQuery && isFetching && docResults.length === 0 && entityResults.length === 0 && relResults.length === 0 && (
          <div style={{ padding: '24px 12px', textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
            Searching...
          </div>
        )}

        {hasQuery &&
          !isFetching &&
          ((activeTab === 'documents' && docResults.length === 0) ||
            (activeTab === 'entities' && entityResults.length === 0) ||
            (activeTab === 'relationships' && relResults.length === 0)) && (
            <div style={{ padding: '24px 12px', textAlign: 'center', color: 'var(--text-dim)', fontSize: 11 }}>
              No results found for &lsquo;{debouncedQuery}&rsquo;. Try broadening your search or adjusting filters.
            </div>
          )}

        {/* Document results */}
        {activeTab === 'documents' && docResults.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="data-readout" style={{ color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.1em' }}>
              {docResults.length} of {docTotal} results
            </div>
            {docResults.map((doc) => (
              <button
                key={doc.id}
                onClick={() => handleDocClick(doc.id)}
                className="finding-card"
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '8px 10px',
                  background: 'var(--bg-tertiary, #141c2b)',
                  border: 'none',
                  borderLeft: `2px solid ${confidenceBorderColor(doc.relevance_score)}`,
                  borderRadius: 2,
                  cursor: 'pointer',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                <div
                  style={{
                    fontSize: 11,
                    color: 'var(--text-primary)',
                    fontWeight: 500,
                    marginBottom: 3,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {highlightText(doc.title || '(untitled)', debouncedQuery)}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-dim)', marginBottom: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span>{doc.source_feed}</span>
                  <span>&middot;</span>
                  <span>{doc.published ? new Date(doc.published).toLocaleDateString() : ''}</span>
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-secondary)', marginBottom: 4, display: 'flex', gap: 6 }}>
                  <span style={{ color: 'var(--accent-cyan)', opacity: 0.7 }}>{doc.entity_count} entities</span>
                  <span>&middot;</span>
                  <span style={{ color: 'var(--accent-cyan)', opacity: 0.7 }}>{doc.relationship_count} rels</span>
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: 'var(--text-secondary)',
                    lineHeight: 1.4,
                    overflow: 'hidden',
                    display: '-webkit-box',
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: 'vertical',
                  }}
                >
                  {highlightText(doc.snippet.slice(0, 200), debouncedQuery)}
                </div>
              </button>
            ))}
            {docResults.length < docTotal && (
              <button
                onClick={() => setDocOffset(docResults.length)}
                className="btn-secondary"
                style={{
                  fontSize: 10,
                  padding: '6px 12px',
                  width: '100%',
                  fontFamily: 'var(--font-display)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                }}
                disabled={docFetching}
              >
                {docFetching ? 'Loading...' : 'Load More'}
              </button>
            )}
          </div>
        )}

        {/* Entity results */}
        {activeTab === 'entities' && entityResults.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="data-readout" style={{ color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.1em' }}>
              {entityResults.length} of {entityTotal} results
            </div>
            {entityResults.map((ent, i) => (
              <button
                key={`${ent.entity_text}-${i}`}
                onClick={() => setSelectedElement({ type: 'entity', id: ent.entity_text })}
                className="finding-card"
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '8px 10px',
                  background: 'var(--bg-tertiary, #141c2b)',
                  border: 'none',
                  borderLeft: `2px solid ${confidenceBorderColor(ent.confidence)}`,
                  borderRadius: 2,
                  cursor: 'pointer',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-primary)', fontWeight: 500 }}>
                    {highlightText(ent.entity_text, debouncedQuery)}
                  </span>
                  <span
                    style={{
                      fontSize: 8,
                      background: 'var(--accent-cyan)',
                      color: 'var(--bg-primary)',
                      padding: '1px 4px',
                      borderRadius: 2,
                      fontWeight: 600,
                      letterSpacing: '0.06em',
                    }}
                  >
                    {ent.entity_type}
                  </span>
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-dim)', marginBottom: 2, display: 'flex', gap: 8 }}>
                  <span>{ent.document_count} docs</span>
                  <span>&middot;</span>
                  <span>{ent.source_feeds.length} feeds</span>
                  {ent.location && (
                    <>
                      <span>&middot;</span>
                      <span>{ent.location.name}</span>
                    </>
                  )}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-dim)', display: 'flex', gap: 8 }}>
                  <span>First: {ent.first_seen ? new Date(ent.first_seen).toLocaleDateString() : '—'}</span>
                  <span>Last: {ent.last_seen ? new Date(ent.last_seen).toLocaleDateString() : '—'}</span>
                  <span style={{ marginLeft: 'auto', color: confidenceBorderColor(ent.confidence) }}>
                    {(ent.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </button>
            ))}
            {entityResults.length < entityTotal && (
              <button
                onClick={() => setEntityOffset(entityResults.length)}
                className="btn-secondary"
                style={{
                  fontSize: 10,
                  padding: '6px 12px',
                  width: '100%',
                  fontFamily: 'var(--font-display)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                }}
                disabled={entityFetching}
              >
                {entityFetching ? 'Loading...' : 'Load More'}
              </button>
            )}
          </div>
        )}

        {/* Relationship results */}
        {activeTab === 'relationships' && relResults.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="data-readout" style={{ color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.1em' }}>
              {relResults.length} of {relTotal} results
            </div>
            {relResults.map((rel, i) => (
              <div
                key={`${rel.subject_text}-${rel.predicate}-${rel.object_text}-${i}`}
                className="finding-card"
                style={{
                  padding: '8px 10px',
                  background: 'var(--bg-tertiary, #141c2b)',
                  borderLeft: `2px solid ${confidenceBorderColor(rel.confidence)}`,
                  borderRadius: 2,
                  fontFamily: 'var(--font-mono)',
                }}
              >
                <div style={{ fontSize: 11, marginBottom: 3, display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                  <button
                    onClick={() => setSelectedElement({ type: 'entity', id: rel.subject_text })}
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--accent-cyan)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      padding: 0,
                    }}
                  >
                    {highlightText(rel.subject_text, debouncedQuery)}
                  </button>
                  <span style={{ color: 'var(--text-dim)', fontSize: 9 }}>&rarr;</span>
                  <span style={{ color: 'var(--accent-amber)', fontSize: 10 }}>
                    {highlightText(rel.predicate, debouncedQuery)}
                  </span>
                  <span style={{ color: 'var(--text-dim)', fontSize: 9 }}>&rarr;</span>
                  <button
                    onClick={() => setSelectedElement({ type: 'entity', id: rel.object_text })}
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--accent-cyan)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      padding: 0,
                    }}
                  >
                    {highlightText(rel.object_text, debouncedQuery)}
                  </button>
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-dim)', display: 'flex', gap: 8 }}>
                  <span>{rel.document_count} docs</span>
                  {rel.extraction_method && (
                    <>
                      <span>&middot;</span>
                      <span>{rel.extraction_method}</span>
                    </>
                  )}
                  <span style={{ marginLeft: 'auto', color: confidenceBorderColor(rel.confidence) }}>
                    {(rel.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
            {relResults.length < relTotal && (
              <button
                onClick={() => setRelOffset(relResults.length)}
                className="btn-secondary"
                style={{
                  fontSize: 10,
                  padding: '6px 12px',
                  width: '100%',
                  fontFamily: 'var(--font-display)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                }}
                disabled={relFetching}
              >
                {relFetching ? 'Loading...' : 'Load More'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
