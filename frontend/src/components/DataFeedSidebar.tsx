// ============================================
// DataFeedSidebar — left panel with recent documents feed
// ============================================

import React, { useEffect, useState, useCallback } from 'react'
import { peripheryApi } from '../api/client'
import { useStore } from '../store'
import type { DocumentSearchResult } from '../api/types'
import { ClassificationBadge } from './records/ClassificationBadge'

const CATEGORY_COLORS: Record<string, string> = {
  government: 'border-l-blue-500',
  news: 'border-l-gray-400',
  cyber: 'border-l-green-500',
  academic: 'border-l-purple-500',
  conflict: 'border-l-amber-500',
}

export const DataFeedSidebar: React.FC = () => {
  const feedSidebarWidth = useStore((s) => s.feedSidebarWidth)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const [docs, setDocs] = useState<DocumentSearchResult[]>([])
  const [loading, setLoading] = useState(true)

  const fetchRecent = useCallback(async () => {
    try {
      const res = await peripheryApi.searchDocuments({ q: '*', limit: 30 })
      setDocs(res.results)
    } catch {
      // Silently fail
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRecent()
    const interval = setInterval(fetchRecent, 60_000)
    return () => clearInterval(interval)
  }, [fetchRecent])

  return (
    <aside
      className="panel flex flex-col shrink-0 h-full overflow-hidden"
      style={{ width: feedSidebarWidth }}
    >
      <div className="panel-header">
        <div className="panel-title">
          <span className="panel-indicator" />
          DATA FEED
        </div>
        <span className="data-readout">{docs.length}</span>
      </div>

      <div className="panel-body flex-1 overflow-y-auto space-y-0.5 !p-1">
        {loading ? (
          <div className="p-4 text-center">
            <div className="calibrating w-16 mx-auto mb-2" />
            <span className="data-readout">LOADING…</span>
          </div>
        ) : docs.length === 0 ? (
          <div className="p-4 text-center">
            <span className="data-readout text-text-dim">NO DOCUMENTS</span>
          </div>
        ) : (
          docs.map((doc) => (
            <FeedItem
              key={doc.id}
              doc={doc}
              onClick={() => setSelectedElement({ type: 'document', id: doc.id, data: { id: doc.id, content: doc.snippet, metadata: doc.metadata || {}, created_at: doc.published } })}
            />
          ))
        )}
      </div>
    </aside>
  )
}

const FeedItem: React.FC<{
  doc: DocumentSearchResult
  onClick: () => void
}> = ({ doc, onClick }) => {
  const category = doc.source_category || 'news'
  const borderClass = CATEGORY_COLORS[category] || 'border-l-gray-600'

  return (
    <button
      onClick={onClick}
      className={`
        w-full text-left px-2 py-1.5 border-l-2 ${borderClass}
        hover:bg-base-500/30 transition-colors
      `}
    >
      <div className="flex items-start gap-1.5">
        <div className="flex-1 min-w-0">
          <p className="text-xxs text-text-primary truncate font-medium leading-tight">
            {doc.title || 'Untitled'}
          </p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-xxs font-mono text-text-dim truncate">
              {doc.source_feed || doc.source_category}
            </span>
            <span className="text-xxs text-text-dim">
              {formatTimeAgo(doc.published)}
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-0.5 shrink-0">
          <ClassificationBadge classification={doc.data_classification} size="sm" />
          {doc.entity_count > 0 && (
            <span className="text-xxs font-mono text-text-dim">{doc.entity_count}e</span>
          )}
        </div>
      </div>
    </button>
  )
}

function formatTimeAgo(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    const now = Date.now()
    const diff = now - d.getTime()
    if (diff < 60_000) return 'now'
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`
    return `${Math.floor(diff / 86_400_000)}d`
  } catch {
    return ''
  }
}

export default DataFeedSidebar
