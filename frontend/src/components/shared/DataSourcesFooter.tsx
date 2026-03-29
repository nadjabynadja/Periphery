// ============================================
// DataSourcesFooter — shows data source attribution
// ============================================

import React from 'react'

interface Props {
  sources?: string[]
  count?: number
  className?: string
}

export const DataSourcesFooter: React.FC<Props> = ({
  sources = [],
  count,
  className = '',
}) => {
  return (
    <div className={`flex items-center gap-2 text-xxs font-mono text-text-dim ${className}`}>
      <span>⊡</span>
      {count != null && <span>{count} sources</span>}
      {sources.length > 0 && (
        <span className="truncate">
          {sources.slice(0, 3).join(' • ')}
          {sources.length > 3 && ` +${sources.length - 3}`}
        </span>
      )}
    </div>
  )
}

export default DataSourcesFooter
