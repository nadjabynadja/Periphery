// ============================================
// ClassificationBadge — data classification indicator
// ============================================

import React from 'react'
import type { DataClassification } from '../../api/types'

interface Props {
  classification?: DataClassification | string | null
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const CLASSIFICATION_STYLES: Record<string, { bg: string; text: string; border: string; glow: string }> = {
  PUBLIC: { bg: 'bg-green-900/30', text: 'text-green-400', border: 'border-green-700/50', glow: '' },
  PII: { bg: 'bg-amber-900/30', text: 'text-amber-400', border: 'border-amber-700/50', glow: '' },
  CUI: { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-700/50', glow: 'shadow-[0_0_6px_rgba(255,68,68,0.3)]' },
  PROPRIETARY: { bg: 'bg-purple-900/30', text: 'text-purple-400', border: 'border-purple-700/50', glow: '' },
  CLASSIFIED: { bg: 'bg-gray-900/80', text: 'text-gray-300', border: 'border-gray-600/50', glow: 'shadow-[0_0_6px_rgba(0,0,0,0.5)]' },
}

const SIZE_CLASSES = {
  sm: 'text-xxs px-1.5 py-0.5',
  md: 'text-xs px-2 py-0.5',
  lg: 'text-xs px-2.5 py-1',
}

export const ClassificationBadge: React.FC<Props> = ({ classification, size = 'sm', className = '' }) => {
  if (!classification) return null

  const upper = String(classification).toUpperCase()
  const style = CLASSIFICATION_STYLES[upper] || CLASSIFICATION_STYLES.PUBLIC

  return (
    <span
      className={`
        inline-flex items-center font-mono font-semibold tracking-wider uppercase
        border rounded-sm select-none
        ${style.bg} ${style.text} ${style.border} ${style.glow}
        ${SIZE_CLASSES[size]}
        ${className}
      `}
    >
      {upper}
    </span>
  )
}

/** Check if user's classification scope includes this classification */
export function isClassificationAllowed(
  classification: DataClassification | string | undefined | null,
  scope: string[],
): boolean {
  if (!classification) return true
  return scope.includes(classification)
}

export default ClassificationBadge
