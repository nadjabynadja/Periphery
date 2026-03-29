// ============================================
// FieldRenderer — renders individual fields by detected type
// ============================================

import React from 'react'
import { type FieldType, detectFieldType, humanizeKey } from './field-config'

interface Props {
  fieldKey: string
  value: unknown
  label?: string
  icon?: string
  typeOverride?: FieldType
  compact?: boolean
}

export const FieldRenderer: React.FC<Props> = ({
  fieldKey,
  value,
  label,
  icon,
  typeOverride,
  compact = false,
}) => {
  if (value === null || value === undefined || value === '') return null

  const fieldType = typeOverride || detectFieldType(fieldKey, value)
  const displayLabel = label || humanizeKey(fieldKey)

  return (
    <div className={`flex ${compact ? 'flex-row items-center gap-2' : 'flex-col gap-0.5'}`}>
      <span className="data-readout flex items-center gap-1 shrink-0">
        {icon && <span className="text-xs">{icon}</span>}
        {displayLabel}
      </span>
      <div className={compact ? 'flex-1 min-w-0' : ''}>
        {renderValue(fieldType, value, fieldKey)}
      </div>
    </div>
  )
}

function renderValue(type: FieldType, value: unknown, key: string): React.ReactNode {
  switch (type) {
    case 'url':
      return (
        <a
          href={String(value)}
          target="_blank"
          rel="noopener noreferrer"
          className="data-value text-accent-cyan hover:underline truncate block"
          title={String(value)}
        >
          {String(value).replace(/^https?:\/\//, '').slice(0, 60)}
          {String(value).replace(/^https?:\/\//, '').length > 60 ? '…' : ''}
        </a>
      )

    case 'date':
      return <span className="data-value">{formatDate(String(value))}</span>

    case 'phone':
      return (
        <a href={`tel:${String(value)}`} className="data-value text-accent-cyan">
          {String(value)}
        </a>
      )

    case 'boolean': {
      const boolVal = value === true || value === 'Y' || value === 'y' || value === '1' || value === 'true'
      return (
        <span className={`data-value ${boolVal ? 'text-accent-amber' : 'text-text-dim'}`}>
          {boolVal ? '● YES' : '○ NO'}
        </span>
      )
    }

    case 'number':
      return <span className="data-value">{Number(value).toLocaleString()}</span>

    case 'array': {
      const arr = Array.isArray(value) ? value : []
      if (arr.length === 0) return <span className="text-text-dim text-xs italic">None</span>

      // Voting history or similar timestamped arrays
      if (arr.length > 0 && typeof arr[0] === 'object') {
        return <ArrayOfObjects items={arr} />
      }

      return (
        <div className="flex flex-wrap gap-1">
          {arr.map((item, i) => (
            <span
              key={i}
              className="text-xs font-mono px-1.5 py-0.5 bg-base-500/50 border border-surface-border rounded-sm text-text-primary"
            >
              {String(item)}
            </span>
          ))}
        </div>
      )
    }

    case 'address':
      return (
        <span className="data-value flex items-start gap-1">
          <span className="text-text-dim text-xs mt-0.5">📍</span>
          {String(value)}
        </span>
      )

    case 'json': {
      if (typeof value === 'object' && value !== null) {
        return (
          <pre className="text-xs font-mono text-text-secondary bg-base-900/50 p-2 rounded-sm overflow-x-auto max-h-32">
            {JSON.stringify(value, null, 2)}
          </pre>
        )
      }
      return <span className="data-value">{String(value)}</span>
    }

    default:
      return <span className="data-value">{String(value)}</span>
  }
}

function formatDate(dateStr: string): string {
  try {
    // Handle MM/DD/YYYY
    if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(dateStr)) {
      return dateStr
    }
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return dateStr
    return d.toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
    })
  } catch {
    return dateStr
  }
}

const ArrayOfObjects: React.FC<{ items: any[] }> = ({ items }) => {
  if (items.length === 0) return null
  const maxShow = 10
  const shown = items.slice(0, maxShow)

  return (
    <div className="space-y-1">
      {shown.map((item, i) => (
        <div key={i} className="text-xs font-mono px-2 py-1 bg-base-900/30 border border-surface-border rounded-sm">
          {Object.entries(item).map(([k, v]) => (
            <span key={k} className="mr-3">
              <span className="text-text-dim">{humanizeKey(k)}:</span>{' '}
              <span className="text-text-primary">{String(v)}</span>
            </span>
          ))}
        </div>
      ))}
      {items.length > maxShow && (
        <span className="text-text-dim text-xxs">
          +{items.length - maxShow} more entries
        </span>
      )}
    </div>
  )
}

export default FieldRenderer
