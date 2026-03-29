// ============================================
// EntityChip — compact entity reference badge
// ============================================

import React from 'react'
import { useStore } from '../../store'

interface Props {
  id?: string
  name: string
  entityType?: string
  confidence?: number
  onClick?: () => void
  className?: string
}

const TYPE_COLORS: Record<string, string> = {
  person: 'border-blue-600/50 text-blue-400',
  organization: 'border-purple-600/50 text-purple-400',
  location: 'border-green-600/50 text-green-400',
  event: 'border-amber-600/50 text-amber-400',
  document: 'border-gray-600/50 text-gray-400',
  default: 'border-accent-cyan/30 text-accent-cyan',
}

export const EntityChip: React.FC<Props> = ({
  id,
  name,
  entityType,
  confidence,
  onClick,
  className = '',
}) => {
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  const handleClick = () => {
    if (onClick) {
      onClick()
    } else if (id) {
      setSelectedElement({ type: 'entity', id })
    }
  }

  const typeKey = entityType?.toLowerCase() || 'default'
  const colorClass = TYPE_COLORS[typeKey] || TYPE_COLORS.default

  return (
    <button
      onClick={handleClick}
      className={`
        inline-flex items-center gap-1 px-1.5 py-0.5
        text-xxs font-mono border rounded-sm
        bg-base-500/20 hover:bg-base-400/30 transition-colors
        cursor-pointer select-none
        ${colorClass} ${className}
      `}
      title={`${entityType || 'entity'}: ${name}${confidence != null ? ` (${(confidence * 100).toFixed(0)}%)` : ''}`}
    >
      {entityType && (
        <span className="opacity-50 uppercase tracking-wider" style={{ fontSize: '0.55rem' }}>
          {entityType.slice(0, 3)}
        </span>
      )}
      <span className="truncate max-w-[120px]">{name}</span>
    </button>
  )
}

export default EntityChip
