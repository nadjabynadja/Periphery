// ============================================
// RecordViewer — Universal Structured Record Viewer
// Renders ANY document's fields intelligently based on source_type
// ============================================

import React, { useMemo, useState } from 'react'
import { ClassificationBadge, isClassificationAllowed } from './ClassificationBadge'
import { FieldRenderer } from './FieldRenderer'
import {
  FIELD_DISPLAY_CONFIG,
  HIDDEN_FIELDS,
  humanizeKey,
  type SourceTypeConfig,
} from './field-config'
import type { DataClassification, Document } from '../../api/types'
import { useStore } from '../../store'

interface RecordViewerProps {
  /** Document metadata fields */
  metadata: Record<string, unknown>
  /** Raw document content (optional) */
  content?: string
  /** Source type override (auto-detected from metadata if not provided) */
  sourceType?: string
  /** Data classification override */
  classification?: DataClassification | string | null
  /** Compact mode for inline/row use */
  compact?: boolean
  /** Title override */
  title?: string
  /** Optional document ID */
  documentId?: string
}

export const RecordViewer: React.FC<RecordViewerProps> = ({
  metadata,
  content,
  sourceType: sourceTypeProp,
  classification: classificationProp,
  compact = false,
  title,
  documentId,
}) => {
  const classificationScope = useStore((s) => s.classificationScope)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(['identity', 'article', 'sanction']))

  // Detect source type
  const sourceType = sourceTypeProp
    || (metadata.source_type as string)
    || (metadata.source_category as string)
    || ''

  // Detect classification
  const classification = classificationProp
    || (metadata.data_classification as string)
    || null

  // Check if confidential
  const isConfidential = metadata.confidential_ind === 'Y' || metadata.confidential_ind === 'y'

  // Check if restricted
  const isRestricted = classification ? !isClassificationAllowed(classification, classificationScope) : false

  // Get source config
  const config: SourceTypeConfig | null = useMemo(() => {
    const key = sourceType.toLowerCase().replace(/[\s-]/g, '_')
    return FIELD_DISPLAY_CONFIG[key] || null
  }, [sourceType])

  // Organize fields into groups
  const { groupedFields, ungroupedFields } = useMemo(() => {
    const allKeys = Object.keys(metadata).filter(k => !HIDDEN_FIELDS.has(k) && k !== 'data_classification' && k !== 'source_type' && k !== 'source_category')

    if (!config) {
      // No config — render all fields alphabetically
      const sorted = allKeys.sort()
      return { groupedFields: new Map<string, string[]>(), ungroupedFields: sorted }
    }

    const grouped = new Map<string, string[]>()
    const assigned = new Set<string>()

    // Initialize groups
    for (const g of config.groups) {
      grouped.set(g.key, [])
    }

    // First, assign primary fields in order
    for (const key of config.primaryFields) {
      if (metadata[key] !== undefined && metadata[key] !== null && metadata[key] !== '') {
        const override = config.fieldOverrides[key]
        const group = override?.group
        if (group && grouped.has(group)) {
          grouped.get(group)!.push(key)
          assigned.add(key)
        }
      }
    }

    // Then assign remaining fields that have overrides
    for (const key of allKeys) {
      if (assigned.has(key)) continue
      const override = config.fieldOverrides[key]
      if (override?.group && grouped.has(override.group)) {
        grouped.get(override.group)!.push(key)
        assigned.add(key)
      }
    }

    // Remaining unassigned fields
    const ungrouped = allKeys.filter(k => !assigned.has(k)).sort()

    return { groupedFields: grouped, ungroupedFields: ungrouped }
  }, [metadata, config])

  const toggleGroup = (key: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (isRestricted) {
    return (
      <div className={`panel ${compact ? 'p-2' : 'p-4'}`}>
        <div className="flex items-center gap-2 mb-2">
          <ClassificationBadge classification={classification} size={compact ? 'sm' : 'md'} />
          <span className="text-text-dim text-xs">RESTRICTED ACCESS</span>
        </div>
        <div className="text-text-dim text-sm opacity-50 italic">
          Your classification scope does not include access to {classification} records.
        </div>
      </div>
    )
  }

  if (compact) {
    return <CompactView
      metadata={metadata}
      config={config}
      classification={classification}
      isConfidential={isConfidential}
      sourceType={sourceType}
      title={title}
    />
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        {config && <span className="text-sm">{config.icon}</span>}
        <span className="panel-header text-text-primary !text-xs !py-0 !px-0 !border-0">
          {title || config?.label || humanizeKey(sourceType) || 'Document'}
        </span>
        <ClassificationBadge classification={classification} size="md" />
        {isConfidential && (
          <span className="inline-flex items-center gap-1 text-xxs font-mono text-amber-400 bg-amber-900/20 border border-amber-700/30 px-1.5 py-0.5 rounded-sm">
            🔒 CONFIDENTIAL
          </span>
        )}
        {documentId && (
          <span className="text-text-dim text-xxs font-mono ml-auto">{documentId}</span>
        )}
      </div>

      {/* Grouped fields */}
      {config && config.groups.map(group => {
        const fields = groupedFields.get(group.key) || []
        if (fields.length === 0) return null
        const isExpanded = expandedGroups.has(group.key)

        return (
          <div key={group.key} className="border border-surface-border rounded-sm overflow-hidden">
            <button
              onClick={() => toggleGroup(group.key)}
              className="w-full flex items-center gap-2 px-3 py-1.5 bg-base-700/50 hover:bg-base-600/50 transition-colors"
            >
              <span className="text-xs">{group.icon}</span>
              <span className="text-xxs font-display font-semibold tracking-wider uppercase text-text-secondary">
                {group.label}
              </span>
              <span className="text-text-dim text-xxs ml-auto">
                {isExpanded ? '▾' : '▸'} {fields.length}
              </span>
            </button>
            {isExpanded && (
              <div className="p-3 space-y-2 bg-base-800/30">
                {fields.map(key => {
                  const override = config.fieldOverrides[key]
                  return (
                    <FieldRenderer
                      key={key}
                      fieldKey={key}
                      value={metadata[key]}
                      label={override?.label}
                      icon={override?.icon}
                      typeOverride={override?.type}
                      compact
                    />
                  )
                })}
              </div>
            )}
          </div>
        )
      })}

      {/* Ungrouped fields */}
      {ungroupedFields.length > 0 && (
        <div className="border border-surface-border rounded-sm overflow-hidden">
          <div className="px-3 py-1.5 bg-base-700/50">
            <span className="text-xxs font-display font-semibold tracking-wider uppercase text-text-secondary">
              {config ? 'Other Fields' : 'All Fields'}
            </span>
          </div>
          <div className="p-3 space-y-2 bg-base-800/30">
            {ungroupedFields.map(key => (
              <FieldRenderer
                key={key}
                fieldKey={key}
                value={metadata[key]}
                compact
              />
            ))}
          </div>
        </div>
      )}

      {/* Content preview */}
      {content && (
        <div className="border border-surface-border rounded-sm overflow-hidden">
          <div className="px-3 py-1.5 bg-base-700/50">
            <span className="text-xxs font-display font-semibold tracking-wider uppercase text-text-secondary">
              📄 Content
            </span>
          </div>
          <div className="p-3 bg-base-800/30">
            <pre className="text-xs font-mono text-text-secondary whitespace-pre-wrap max-h-48 overflow-y-auto">
              {content.slice(0, 2000)}
              {content.length > 2000 ? '\n\n… (truncated)' : ''}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// Compact view for inline / search result rows
const CompactView: React.FC<{
  metadata: Record<string, unknown>
  config: SourceTypeConfig | null
  classification: string | null
  isConfidential: boolean
  sourceType: string
  title?: string
}> = ({ metadata, config, classification, isConfidential, sourceType, title }) => {
  // Show top 3-4 primary fields inline
  const primaryKeys = config?.primaryFields.slice(0, 4) || Object.keys(metadata).slice(0, 4)

  return (
    <div className="flex items-center gap-2 min-w-0">
      {config && <span className="text-xs shrink-0">{config.icon}</span>}
      <span className="text-xs text-text-primary font-medium truncate">
        {title || (metadata.title as string) || (metadata.name as string) ||
         (metadata.first_name ? `${metadata.first_name} ${metadata.last_name || ''}` : '') ||
         humanizeKey(sourceType) || 'Document'}
      </span>
      <ClassificationBadge classification={classification} size="sm" />
      {isConfidential && <span className="text-amber-400 text-xxs">🔒</span>}
      <div className="flex items-center gap-2 ml-auto shrink-0">
        {primaryKeys.slice(1, 3).map(key => {
          const val = metadata[key]
          if (!val) return null
          return (
            <span key={key} className="text-xxs font-mono text-text-dim truncate max-w-[100px]">
              {String(val)}
            </span>
          )
        })}
      </div>
    </div>
  )
}

export default RecordViewer
