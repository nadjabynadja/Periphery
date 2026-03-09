import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { useStore } from '../../store'

const LANE_HEIGHT = 28
const HEADER_HEIGHT = 40
const MIN_BAR_WIDTH = 2

interface TimelineEntity {
  id: string
  name: string
  type: string
  confidence: number
  firstSeen: number
  lastSeen: number
  clusterIds: string[]
}

interface TimelineRelationship {
  id: string
  subjectId: string
  objectId: string
  predicate: string
  confidence: number
  firstSeen: number
  lastSeen: number
}

export function TemporalTimeline() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const snapshot = useStore(s => s.snapshot)
  const setSelectedElement = useStore(s => s.setSelectedElement)
  const highlightedEntityIds = useStore(s => s.highlightedEntityIds)

  const [viewStart, setViewStart] = useState(0)
  const [viewEnd, setViewEnd] = useState(0)
  const [scrollY, setScrollY] = useState(0)
  const [hoveredEntity, setHoveredEntity] = useState<string | null>(null)
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })

  // Parse entities into timeline format
  const timelineData = useMemo(() => {
    if (!snapshot) return { entities: [], relationships: [], minTime: 0, maxTime: 0 }

    const entities: TimelineEntity[] = snapshot.entities.map(e => ({
      id: e.canonical_id,
      name: e.name,
      type: e.entity_type,
      confidence: e.confidence,
      firstSeen: new Date(e.first_seen).getTime(),
      lastSeen: new Date(e.last_seen).getTime(),
      clusterIds: e.cluster_ids,
    }))

    const relationships: TimelineRelationship[] = snapshot.relationships.map(r => ({
      id: r.id,
      subjectId: r.subject_id,
      objectId: r.object_id,
      predicate: r.predicate,
      confidence: r.confidence,
      firstSeen: new Date(r.first_seen).getTime(),
      lastSeen: new Date(r.last_seen).getTime(),
    }))

    const allTimes = [
      ...entities.flatMap(e => [e.firstSeen, e.lastSeen]),
      ...relationships.flatMap(r => [r.firstSeen, r.lastSeen]),
    ].filter(t => t > 0 && isFinite(t))

    const minTime = allTimes.length ? Math.min(...allTimes) : Date.now() - 86400000 * 30
    const maxTime = allTimes.length ? Math.max(...allTimes) : Date.now()

    // Sort by first seen, then confidence
    entities.sort((a, b) => a.firstSeen - b.firstSeen || b.confidence - a.confidence)

    return { entities, relationships, minTime, maxTime }
  }, [snapshot])

  // Initialize view range
  useEffect(() => {
    if (timelineData.minTime && timelineData.maxTime) {
      const padding = (timelineData.maxTime - timelineData.minTime) * 0.05 || 86400000
      setViewStart(timelineData.minTime - padding)
      setViewEnd(timelineData.maxTime + padding)
    }
  }, [timelineData.minTime, timelineData.maxTime])

  const timeToX = useCallback((time: number, width: number) => {
    const range = viewEnd - viewStart
    if (range <= 0) return 0
    return ((time - viewStart) / range) * width
  }, [viewStart, viewEnd])

  // Render
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const rect = container.getBoundingClientRect()
    const width = rect.width
    const height = rect.height
    const dpr = window.devicePixelRatio || 1

    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    const ctx = canvas.getContext('2d')!
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, height)

    const labelWidth = 140
    const timelineWidth = width - labelWidth
    const { entities, relationships } = timelineData

    // Background
    ctx.fillStyle = '#0a0e17'
    ctx.fillRect(0, 0, width, height)

    // Time axis header
    ctx.fillStyle = '#0f1520'
    ctx.fillRect(labelWidth, 0, timelineWidth, HEADER_HEIGHT)
    ctx.strokeStyle = '#1e293b'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(labelWidth, HEADER_HEIGHT)
    ctx.lineTo(width, HEADER_HEIGHT)
    ctx.stroke()

    // Time ticks
    const range = viewEnd - viewStart
    const tickInterval = getTickInterval(range)
    const firstTick = Math.ceil(viewStart / tickInterval) * tickInterval

    ctx.font = '10px "JetBrains Mono", monospace'
    ctx.fillStyle = '#475569'
    ctx.textAlign = 'center'

    for (let t = firstTick; t <= viewEnd; t += tickInterval) {
      const x = labelWidth + timeToX(t, timelineWidth)
      if (x < labelWidth || x > width) continue

      // Tick line
      ctx.strokeStyle = '#1e293b'
      ctx.beginPath()
      ctx.moveTo(x, HEADER_HEIGHT)
      ctx.lineTo(x, height)
      ctx.stroke()

      // Tick label
      const date = new Date(t)
      const label = formatTickLabel(date, tickInterval)
      ctx.fillStyle = '#475569'
      ctx.fillText(label, x, HEADER_HEIGHT - 8)
    }

    // Label column background
    ctx.fillStyle = '#0d1220'
    ctx.fillRect(0, 0, labelWidth, height)
    ctx.strokeStyle = '#1e293b'
    ctx.beginPath()
    ctx.moveTo(labelWidth, 0)
    ctx.lineTo(labelWidth, height)
    ctx.stroke()

    // Entity swim lanes
    const visibleStart = Math.floor(scrollY / LANE_HEIGHT)
    const visibleEnd = Math.min(entities.length, visibleStart + Math.ceil(height / LANE_HEIGHT) + 1)

    for (let i = visibleStart; i < visibleEnd; i++) {
      const entity = entities[i]
      const y = HEADER_HEIGHT + (i - scrollY / LANE_HEIGHT) * LANE_HEIGHT

      if (y + LANE_HEIGHT < HEADER_HEIGHT || y > height) continue

      const isHighlighted = highlightedEntityIds.size === 0 || highlightedEntityIds.has(entity.id)
      const isHovered = hoveredEntity === entity.id
      const conf = entity.confidence
      const color = conf >= 0.6 ? '#00D4FF' : conf >= 0.4 ? '#FFB833' : '#3A4A5C'
      const alpha = isHighlighted ? Math.max(0.2, conf) : 0.08

      // Lane background on hover
      if (isHovered) {
        ctx.fillStyle = '#1e293b22'
        ctx.fillRect(0, y, width, LANE_HEIGHT)
      }

      // Lane separator
      ctx.strokeStyle = '#1e293b44'
      ctx.beginPath()
      ctx.moveTo(0, y + LANE_HEIGHT)
      ctx.lineTo(width, y + LANE_HEIGHT)
      ctx.stroke()

      // Entity label
      ctx.font = '11px "JetBrains Mono", monospace'
      ctx.fillStyle = isHighlighted ? color : '#475569'
      ctx.textAlign = 'right'
      ctx.globalAlpha = isHighlighted ? 1 : 0.5
      const name = entity.name.length > 16 ? entity.name.substring(0, 15) + '\u2026' : entity.name
      ctx.fillText(name, labelWidth - 8, y + LANE_HEIGHT / 2 + 4)
      ctx.globalAlpha = 1

      // Time bar
      const barStart = labelWidth + timeToX(entity.firstSeen, timelineWidth)
      const barEnd = labelWidth + timeToX(entity.lastSeen, timelineWidth)
      const barWidth = Math.max(MIN_BAR_WIDTH, barEnd - barStart)
      const barY = y + 6
      const barHeight = LANE_HEIGHT - 12

      // Glow for high confidence
      if (conf >= 0.6 && isHighlighted) {
        ctx.shadowBlur = 6
        ctx.shadowColor = color
      }

      ctx.fillStyle = color
      ctx.globalAlpha = alpha
      ctx.fillRect(barStart, barY, barWidth, barHeight)
      ctx.globalAlpha = 1
      ctx.shadowBlur = 0

      // Border
      if (conf >= 0.6) {
        ctx.strokeStyle = color
        ctx.globalAlpha = alpha * 1.5
        ctx.lineWidth = 1
        ctx.strokeRect(barStart, barY, barWidth, barHeight)
        ctx.globalAlpha = 1
      }

      // Pulse for emerging entities
      if (conf < 0.4 && conf >= 0.2 && isHighlighted) {
        const pulse = (Math.sin(Date.now() / 2000 + i) + 1) / 2
        ctx.fillStyle = color
        ctx.globalAlpha = pulse * 0.15
        ctx.fillRect(barStart - 2, barY - 2, barWidth + 4, barHeight + 4)
        ctx.globalAlpha = 1
      }
    }

    // Render relationships as vertical/diagonal lines
    for (const rel of relationships) {
      const subjectIdx = entities.findIndex(e => e.id === rel.subjectId)
      const objectIdx = entities.findIndex(e => e.id === rel.objectId)
      if (subjectIdx === -1 || objectIdx === -1) continue

      const x = labelWidth + timeToX((rel.firstSeen + rel.lastSeen) / 2, timelineWidth)
      if (x < labelWidth || x > width) continue

      const y1 = HEADER_HEIGHT + (subjectIdx - scrollY / LANE_HEIGHT) * LANE_HEIGHT + LANE_HEIGHT / 2
      const y2 = HEADER_HEIGHT + (objectIdx - scrollY / LANE_HEIGHT) * LANE_HEIGHT + LANE_HEIGHT / 2

      const conf = rel.confidence
      const color = conf >= 0.6 ? '#00D4FF' : conf >= 0.4 ? '#FFB833' : '#3A4A5C'

      ctx.strokeStyle = color
      ctx.globalAlpha = Math.max(0.1, conf * 0.4)
      ctx.lineWidth = 0.5 + conf
      ctx.setLineDash(conf < 0.4 ? [3, 3] : [])
      ctx.beginPath()
      ctx.moveTo(x, y1)
      ctx.lineTo(x, y2)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.globalAlpha = 1
    }
  }, [snapshot, timelineData, viewStart, viewEnd, scrollY, hoveredEntity, highlightedEntityIds, timeToX])

  // Mouse interactions
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    if (e.ctrlKey || e.metaKey) {
      // Zoom
      const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9
      const range = viewEnd - viewStart
      const mid = (viewStart + viewEnd) / 2
      const newRange = range * zoomFactor
      setViewStart(mid - newRange / 2)
      setViewEnd(mid + newRange / 2)
    } else {
      // Scroll vertically
      setScrollY(prev => Math.max(0, prev + e.deltaY))
    }
  }, [viewStart, viewEnd])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const y = e.clientY - rect.top - HEADER_HEIGHT
    const idx = Math.floor((y + scrollY) / LANE_HEIGHT)
    const entity = timelineData.entities[idx]
    setHoveredEntity(entity?.id || null)
    setMousePos({ x: e.clientX, y: e.clientY })
  }, [scrollY, timelineData.entities])

  const handleClick = useCallback((e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const y = e.clientY - rect.top - HEADER_HEIGHT
    const idx = Math.floor((y + scrollY) / LANE_HEIGHT)
    const entity = timelineData.entities[idx]
    if (entity) {
      setSelectedElement({ type: 'entity', id: entity.id })
    }
  }, [scrollY, timelineData.entities, setSelectedElement])

  const handleDoubleClick = useCallback(() => {
    // Reset zoom
    if (timelineData.minTime && timelineData.maxTime) {
      const padding = (timelineData.maxTime - timelineData.minTime) * 0.05 || 86400000
      setViewStart(timelineData.minTime - padding)
      setViewEnd(timelineData.maxTime + padding)
    }
    setScrollY(0)
  }, [timelineData.minTime, timelineData.maxTime])

  if (!snapshot || snapshot.entities.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="data-readout mb-2">No temporal data available</div>
          <div className="text-xxs text-text-dim">Entities with timestamps will appear on this timeline</div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full" ref={containerRef}>
      <canvas
        ref={canvasRef}
        className="absolute inset-0"
        onWheel={handleWheel}
        onMouseMove={handleMouseMove}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        style={{ cursor: hoveredEntity ? 'pointer' : 'default' }}
      />

      {/* Tooltip */}
      {hoveredEntity && (
        <div
          className="fixed z-50 pointer-events-none"
          style={{ left: mousePos.x + 12, top: mousePos.y - 8 }}
        >
          <div className="bg-base-800 border border-surface-border px-2 py-1.5" style={{ borderRadius: '2px' }}>
            {(() => {
              const entity = timelineData.entities.find(e => e.id === hoveredEntity)
              if (!entity) return null
              const color = entity.confidence >= 0.6 ? '#00D4FF' : entity.confidence >= 0.4 ? '#FFB833' : '#3A4A5C'
              return (
                <>
                  <div className="font-mono text-xxs" style={{ color }}>{entity.name}</div>
                  <div className="text-xxs text-text-dim mt-0.5">
                    {entity.type} · {(entity.confidence * 100).toFixed(0)}%
                  </div>
                  <div className="text-xxs text-text-dim">
                    {new Date(entity.firstSeen).toLocaleDateString()} — {new Date(entity.lastSeen).toLocaleDateString()}
                  </div>
                </>
              )
            })()}
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="absolute top-1 right-1 flex gap-1 z-10">
        <button
          onClick={() => {
            const range = viewEnd - viewStart
            setViewStart(viewStart - range * 0.25)
            setViewEnd(viewEnd - range * 0.25)
          }}
          className="btn-secondary px-2 py-0.5 text-xxs"
        >
          \u25C0
        </button>
        <button
          onClick={() => {
            const mid = (viewStart + viewEnd) / 2
            const range = (viewEnd - viewStart) * 0.75
            setViewStart(mid - range / 2)
            setViewEnd(mid + range / 2)
          }}
          className="btn-secondary px-2 py-0.5 text-xxs"
        >
          +
        </button>
        <button
          onClick={() => {
            const mid = (viewStart + viewEnd) / 2
            const range = (viewEnd - viewStart) * 1.33
            setViewStart(mid - range / 2)
            setViewEnd(mid + range / 2)
          }}
          className="btn-secondary px-2 py-0.5 text-xxs"
        >
          -
        </button>
        <button
          onClick={() => {
            const range = viewEnd - viewStart
            setViewStart(viewStart + range * 0.25)
            setViewEnd(viewEnd + range * 0.25)
          }}
          className="btn-secondary px-2 py-0.5 text-xxs"
        >
          \u25B6
        </button>
      </div>

      {/* Legend */}
      <div className="absolute bottom-2 left-2 z-10 flex gap-3 items-center">
        <div className="flex items-center gap-1">
          <div className="w-3 h-2" style={{ backgroundColor: '#00D4FF', opacity: 0.8 }} />
          <span className="text-xxs text-text-dim">High confidence</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-2" style={{ backgroundColor: '#FFB833', opacity: 0.6 }} />
          <span className="text-xxs text-text-dim">Medium</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-2" style={{ backgroundColor: '#3A4A5C', opacity: 0.35 }} />
          <span className="text-xxs text-text-dim">Low</span>
        </div>
      </div>
    </div>
  )
}

function getTickInterval(range: number): number {
  const hour = 3600000
  const day = 86400000
  if (range < hour * 6) return hour
  if (range < day * 2) return hour * 6
  if (range < day * 14) return day
  if (range < day * 60) return day * 7
  if (range < day * 365) return day * 30
  return day * 90
}

function formatTickLabel(date: Date, interval: number): string {
  const day = 86400000
  if (interval < day) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  if (interval < day * 30) {
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }
  return date.toLocaleDateString([], { month: 'short', year: '2-digit' })
}
