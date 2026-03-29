// ============================================
// SearchPanel — document/entity/relationship search with facets
// ============================================

import React, { useState, useCallback, useEffect } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'
import type {
  DocumentSearchResult,
  EntitySearchResult,
  RelationshipSearchResult,
  FacetsResponse,
} from '../../api/types'
import { ClassificationBadge } from '../records/ClassificationBadge'
import { ConfidenceBadge } from '../shared/ConfidenceBadge'

type SearchTab = 'documents' | 'entities' | 'relationships'

export const SearchPanel: React.FC = () => {
  const searchPanelOpen = useStore((s) => s.searchPanelOpen)
  const setSearchPanelOpen = useStore((s) => s.setSearchPanelOpen)
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  const [tab, setTab] = useState<SearchTab>('documents')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)

  // Results
  const [docResults, setDocResults] = useState<DocumentSearchResult[]>([])
  const [entityResults, setEntityResults] = useState<EntitySearchResult[]>([])
  const [relResults, setRelResults] = useState<RelationshipSearchResult[]>([])
  const [totalCount, setTotalCount] = useState(0)

  // Facets
  const [facets, setFacets] = useState<FacetsResponse | null>(null)
  const [selectedFeed, setSelectedFeed] = useState<string>('')
  const [selectedCategory, setSelectedCategory] = useState<string>('')

  useEffect(() => {
    if (searchPanelOpen) {
      peripheryApi.searchFacets().then(setFacets).catch(() => {})
    }
  }, [searchPanelOpen])

  const search = useCallback(async () => {
    if (!query.trim()) return
    setLoading(true)

    try {
      if (tab === 'documents') {
        const res = await peripheryApi.searchDocuments({
          q: query,
          source_feed: selectedFeed || undefined,
          category: selectedCategory || undefined,
          limit: 25,
        })
        setDocResults(res.results)
        setTotalCount(res.total_count)
      } else if (tab === 'entities') {
        const res = await peripheryApi.searchEntities({ q: query, limit: 25 })
        setEntityResults(res.results)
        setTotalCount(res.total_count)
      } else {
        const res = await peripheryApi.searchRelationships({ q: query, limit: 25 })
        setRelResults(res.results)
        setTotalCount(res.total_count)
      }
    } catch {
      // Search failed
    } finally {
      setLoading(false)
    }
  }, [query, tab, selectedFeed, selectedCategory])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') search()
    if (e.key === 'Escape') setSearchPanelOpen(false)
  }

  if (!searchPanelOpen) return null

  return (
    <div className="absolute inset-0 z-30 bg-base-900/80 flex items-start justify-center pt-12">
      <div className="panel w-full max-w-3xl max-h-[80vh] flex flex-col mx-4 shadow-2xl">
        {/* Header */}
        <div className="panel-header">
          <div className="panel-title">
            <span className="panel-indicator" />
            SEARCH
          </div>
          <button
            onClick={() => setSearchPanelOpen(false)}
            className="text-text-dim hover:text-text-primary transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Search input */}
        <div className="p-3 border-b border-surface-border">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search documents, entities, relationships…"
            className="command-input"
            autoFocus
          />

          {/* Tabs + facets */}
          <div className="flex items-center gap-2 mt-2">
            <div className="tab-bar !border-b-0 flex-1">
              {(['documents', 'entities', 'relationships'] as const).map((t) => (
                <button
                  key={t}
                  className={`tab-item ${tab === t ? 'active' : ''}`}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            {tab === 'documents' && facets && (
              <div className="flex items-center gap-1">
                <select
                  value={selectedFeed}
                  onChange={(e) => setSelectedFeed(e.target.value)}
                  className="command-input !py-0.5 !px-1 !text-xxs !w-auto"
                >
                  <option value="">All feeds</option>
                  {facets.source_feeds.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name} ({f.count})
                    </option>
                  ))}
                </select>
                <select
                  value={selectedCategory}
                  onChange={(e) => setSelectedCategory(e.target.value)}
                  className="command-input !py-0.5 !px-1 !text-xxs !w-auto"
                >
                  <option value="">All categories</option>
                  {facets.categories.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} ({c.count})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {loading ? (
            <div className="text-center py-8">
              <div className="calibrating w-20 mx-auto mb-2" />
              <span className="data-readout">SEARCHING…</span>
            </div>
          ) : (
            <>
              {totalCount > 0 && (
                <div className="data-readout px-2 py-1">
                  {totalCount} RESULTS
                </div>
              )}

              {tab === 'documents' && docResults.map((doc) => (
                <button
                  key={doc.id}
                  className="w-full text-left px-3 py-2 hover:bg-base-500/30 rounded-sm transition-colors"
                  onClick={() => {
                    setSelectedElement({ type: 'document', id: doc.id, data: { id: doc.id, content: doc.snippet, metadata: doc.metadata || {}, created_at: doc.published } })
                    setSearchPanelOpen(false)
                  }}
                >
                  <div className="flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-text-primary font-medium truncate">{doc.title}</p>
                      <p className="text-xxs text-text-dim mt-0.5 line-clamp-2">{doc.snippet}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xxs font-mono text-text-dim">{doc.source_feed}</span>
                        <span className="text-xxs text-text-dim">{doc.published?.slice(0, 10)}</span>
                      </div>
                    </div>
                    <ClassificationBadge classification={doc.data_classification} size="sm" />
                  </div>
                </button>
              ))}

              {tab === 'entities' && entityResults.map((ent, i) => (
                <button
                  key={i}
                  className="w-full text-left px-3 py-2 hover:bg-base-500/30 rounded-sm transition-colors"
                  onClick={() => {
                    setSelectedElement({ type: 'entity', id: ent.entity_text })
                    setSearchPanelOpen(false)
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-text-primary font-medium">{ent.entity_text}</span>
                    <span className="text-xxs font-mono text-text-dim uppercase">{ent.entity_type}</span>
                    <ConfidenceBadge confidence={ent.confidence} />
                    <span className="text-xxs text-text-dim ml-auto">{ent.document_count} docs</span>
                  </div>
                </button>
              ))}

              {tab === 'relationships' && relResults.map((rel, i) => (
                <div
                  key={i}
                  className="px-3 py-2 hover:bg-base-500/30 rounded-sm transition-colors"
                >
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className="text-text-primary font-medium">{rel.subject_text}</span>
                    <span className="text-accent-amber font-mono text-xxs">→ {rel.predicate} →</span>
                    <span className="text-text-primary font-medium">{rel.object_text}</span>
                    <ConfidenceBadge confidence={rel.confidence} className="ml-auto" />
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default SearchPanel
