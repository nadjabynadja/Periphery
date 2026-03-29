// ============================================
// ConfidenceBadge — shows confidence score with color coding
// ============================================

import React from 'react'

interface Props {
  confidence: number
  showLabel?: boolean
  size?: 'sm' | 'md'
  className?: string
}

export const ConfidenceBadge: React.FC<Props> = ({
  confidence,
  showLabel = false,
  size = 'sm',
  className = '',
}) => {
  const pct = Math.round(confidence * 100)
  const colorClass = confidence >= 0.7
    ? 'text-accent-cyan'
    : confidence >= 0.4
      ? 'text-accent-amber'
      : 'text-accent-red'

  const bgClass = confidence >= 0.7
    ? 'bg-accent-cyan/10'
    : confidence >= 0.4
      ? 'bg-amber-900/20'
      : 'bg-red-900/20'

  const sizeClass = size === 'sm' ? 'text-xxs px-1 py-0.5' : 'text-xs px-1.5 py-0.5'

  return (
    <span
      className={`
        inline-flex items-center gap-1 font-mono rounded-sm
        ${bgClass} ${colorClass} ${sizeClass} ${className}
      `}
      title={`Confidence: ${pct}%`}
    >
      {showLabel && <span className="opacity-60">conf</span>}
      {pct}%
    </span>
  )
}

export default ConfidenceBadge
