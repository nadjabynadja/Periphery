import type { LegibilityTier } from '../../api/types'

const tierColors: Record<string, string> = {
  solid: '#00D4FF',
  defined: '#00D4FF',
  emerging: '#FFB833',
  haze: '#3A4A5C',
  whisper: '#2A3040',
}

interface Props {
  confidence: number
  tier?: LegibilityTier
  showLabel?: boolean
  size?: 'sm' | 'md'
}

export function ConfidenceBadge({ confidence, tier, showLabel = false, size = 'sm' }: Props) {
  const t = tier || getTier(confidence)
  const color = tierColors[t] || '#3A4A5C'
  const pct = (confidence * 100).toFixed(0)

  return (
    <span className="inline-flex items-center gap-1">
      <span
        className="inline-block rounded-full"
        style={{
          width: size === 'sm' ? 6 : 8,
          height: size === 'sm' ? 6 : 8,
          backgroundColor: color,
          boxShadow: confidence >= 0.6 ? `0 0 4px ${color}88` : 'none',
          opacity: Math.max(0.3, confidence),
        }}
      />
      <span
        className="font-mono"
        style={{
          fontSize: size === 'sm' ? '0.6rem' : '0.7rem',
          color,
        }}
      >
        {pct}%
      </span>
      {showLabel && (
        <span className="text-text-dim" style={{ fontSize: '0.6rem' }}>
          {t.toUpperCase()}
        </span>
      )}
    </span>
  )
}

function getTier(confidence: number): LegibilityTier {
  if (confidence >= 0.8) return 'solid'
  if (confidence >= 0.6) return 'defined'
  if (confidence >= 0.4) return 'emerging'
  if (confidence >= 0.2) return 'haze'
  return 'whisper'
}

export function ConfidenceBar({ confidence, width = '100%' }: { confidence: number; width?: string }) {
  const color = confidence >= 0.6 ? '#00D4FF' : confidence >= 0.4 ? '#FFB833' : '#3A4A5C'
  return (
    <div className="h-1 bg-base-500 overflow-hidden" style={{ borderRadius: '1px', width }}>
      <div
        className="h-full transition-all duration-300"
        style={{
          width: `${confidence * 100}%`,
          backgroundColor: color,
          boxShadow: `0 0 4px ${color}44`,
        }}
      />
    </div>
  )
}
