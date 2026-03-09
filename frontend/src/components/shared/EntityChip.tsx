import { ConfidenceBadge } from './ConfidenceBadge'

interface Props {
  name: string
  entityType: string
  confidence: number
  onClick?: () => void
}

const typeIcons: Record<string, string> = {
  person: '\u25CB',
  organization: '\u25A1',
  location: '\u25C7',
  event: '\u25B3',
  document: '\u25C9',
  cluster: '\u25C6',
}

export function EntityChip({ name, entityType, confidence, onClick }: Props) {
  const icon = typeIcons[entityType.toLowerCase()] || '\u25CB'

  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-2 py-0.5 border border-surface-border hover:border-accent-cyan/30 transition-colors"
      style={{ borderRadius: '2px', background: '#0f152010', cursor: onClick ? 'pointer' : 'default' }}
    >
      <span className="text-text-dim" style={{ fontSize: '0.6rem' }}>{icon}</span>
      <span className="font-mono text-text-secondary truncate" style={{ fontSize: '0.65rem', maxWidth: 120 }}>
        {name}
      </span>
      <ConfidenceBadge confidence={confidence} size="sm" />
    </button>
  )
}
