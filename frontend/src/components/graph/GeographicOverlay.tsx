import { useRef, useEffect, useState, useCallback } from 'react'
import { useStore } from '../../store'

// Leaflet CSS is loaded dynamically
let leafletLoaded = false

function loadLeafletCSS() {
  if (leafletLoaded) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'
  document.head.appendChild(link)
  leafletLoaded = true
}

export function GeographicOverlay() {
  const mapRef = useRef<HTMLDivElement>(null)
  const leafletMap = useRef<L.Map | null>(null)
  const markersRef = useRef<L.LayerGroup | null>(null)
  const linesRef = useRef<L.LayerGroup | null>(null)
  const snapshot = useStore(s => s.snapshot)
  const setSelectedElement = useStore(s => s.setSelectedElement)
  const highlightedEntityIds = useStore(s => s.highlightedEntityIds)
  const [heatmapMode, setHeatmapMode] = useState(false)
  const [L, setL] = useState<typeof import('leaflet') | null>(null)

  // Load Leaflet dynamically
  useEffect(() => {
    loadLeafletCSS()
    import('leaflet').then(mod => setL(mod.default || mod))
  }, [])

  // Initialize map
  useEffect(() => {
    if (!L || !mapRef.current || leafletMap.current) return

    const map = L.map(mapRef.current, {
      center: [20, 0],
      zoom: 2,
      zoomControl: false,
      attributionControl: false,
    })

    // CartoDB dark tiles
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      subdomains: 'abcd',
    }).addTo(map)

    // Add zoom control to top-right
    L.control.zoom({ position: 'topright' }).addTo(map)

    markersRef.current = L.layerGroup().addTo(map)
    linesRef.current = L.layerGroup().addTo(map)
    leafletMap.current = map

    return () => {
      map.remove()
      leafletMap.current = null
    }
  }, [L])

  // Update markers when snapshot changes
  useEffect(() => {
    if (!L || !leafletMap.current || !markersRef.current || !linesRef.current || !snapshot) return

    markersRef.current.clearLayers()
    linesRef.current.clearLayers()

    const entityPositions = new Map<string, [number, number]>()

    // Render entities with locations
    for (const entity of snapshot.entities) {
      if (!entity.location) continue

      const { lat, lon } = entity.location
      entityPositions.set(entity.canonical_id, [lat, lon])

      const isHighlighted = highlightedEntityIds.size === 0 || highlightedEntityIds.has(entity.canonical_id)
      const conf = entity.confidence
      const color = conf >= 0.6 ? '#00D4FF' : conf >= 0.4 ? '#FFB833' : '#3A4A5C'
      const opacity = isHighlighted ? Math.max(0.3, conf) : 0.1
      const radius = 4 + conf * 6

      const marker = L.circleMarker([lat, lon], {
        radius,
        fillColor: color,
        color: color,
        weight: conf >= 0.6 ? 1 : 0,
        opacity: opacity,
        fillOpacity: opacity * 0.8,
      })

      marker.bindTooltip(
        `<div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #E2E8F0;">
          <div style="color: ${color}; font-weight: 500;">${entity.name}</div>
          <div style="color: #94A3B8; font-size: 10px;">${entity.entity_type} · ${(conf * 100).toFixed(0)}%</div>
        </div>`,
        {
          className: 'periphery-tooltip',
          direction: 'top',
          offset: [0, -8],
        },
      )

      marker.on('click', () => {
        setSelectedElement({ type: 'entity', id: entity.canonical_id })
      })

      markersRef.current!.addLayer(marker)
    }

    // Render relationships as arced lines between geocoded entities
    if (!heatmapMode) {
      for (const rel of snapshot.relationships) {
        const sourcePos = entityPositions.get(rel.subject_id)
        const targetPos = entityPositions.get(rel.object_id)
        if (!sourcePos || !targetPos) continue

        const conf = rel.confidence
        const color = conf >= 0.6 ? '#00D4FF' : conf >= 0.4 ? '#FFB833' : '#3A4A5C'
        const opacity = Math.max(0.1, conf * 0.5)
        const weight = 0.5 + conf * 1.5

        // Simple straight line (arcs would need a polyline with midpoint offset)
        const midLat = (sourcePos[0] + targetPos[0]) / 2
        const midLon = (sourcePos[1] + targetPos[1]) / 2
        // Add slight curve by offsetting midpoint
        const dx = targetPos[1] - sourcePos[1]
        const dy = targetPos[0] - sourcePos[0]
        const dist = Math.sqrt(dx * dx + dy * dy)
        const arcOffset = dist * 0.15
        const arcLat = midLat + (dx / (dist || 1)) * arcOffset
        const arcLon = midLon - (dy / (dist || 1)) * arcOffset

        const line = L.polyline(
          [sourcePos, [arcLat, arcLon], targetPos],
          {
            color,
            weight,
            opacity,
            dashArray: conf < 0.4 ? '4 4' : undefined,
            smoothFactor: 2,
          },
        )

        line.bindTooltip(
          `<div style="font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #94A3B8;">
            ${rel.predicate} (${(conf * 100).toFixed(0)}%)
          </div>`,
          { sticky: true },
        )

        linesRef.current!.addLayer(line)
      }
    }

    // Render cluster footprints
    for (const cluster of snapshot.clusters) {
      if (!cluster.geographic_footprint || cluster.geographic_footprint.length < 3) continue

      const points: [number, number][] = cluster.geographic_footprint.map(p => [p.lat, p.lon])
      const conf = cluster.confidence
      const color = conf >= 0.6 ? '#00D4FF' : conf >= 0.4 ? '#FFB833' : '#3A4A5C'

      const polygon = L.polygon(points, {
        color: conf >= 0.6 ? color : 'transparent',
        weight: conf >= 0.6 ? 1 : 0,
        fillColor: color,
        fillOpacity: conf >= 0.6 ? 0.1 : 0.05,
      })

      polygon.bindTooltip(
        `<div style="font-family: 'JetBrains Mono', monospace; font-size: 11px;">
          <div style="color: ${color};">${cluster.label}</div>
          <div style="color: #94A3B8; font-size: 10px;">${cluster.status} · ${(conf * 100).toFixed(0)}%</div>
        </div>`,
      )

      polygon.on('click', () => {
        setSelectedElement({ type: 'cluster', id: cluster.cluster_id })
      })

      markersRef.current!.addLayer(polygon)
    }
  }, [L, snapshot, highlightedEntityIds, heatmapMode, setSelectedElement])

  // Inject tooltip styles
  useEffect(() => {
    const style = document.createElement('style')
    style.textContent = `
      .periphery-tooltip {
        background: #0f1520 !important;
        border: 1px solid #1e293b !important;
        border-radius: 2px !important;
        padding: 6px 8px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4) !important;
      }
      .periphery-tooltip::before { border-top-color: #1e293b !important; }
      .leaflet-control-zoom a {
        background: #111827 !important;
        color: #94A3B8 !important;
        border-color: #1e293b !important;
      }
      .leaflet-control-zoom a:hover {
        background: #1a2236 !important;
        color: #00D4FF !important;
      }
    `
    document.head.appendChild(style)
    return () => { document.head.removeChild(style) }
  }, [])

  const entityCount = snapshot?.entities.filter(e => e.location).length || 0

  return (
    <div className="relative w-full h-full">
      <div ref={mapRef} className="absolute inset-0" />

      {/* Controls overlay */}
      <div className="absolute top-2 left-2 z-[1000] flex gap-1">
        <button
          onClick={() => setHeatmapMode(false)}
          className={`px-2 py-1 text-xxs font-display font-semibold tracking-wider uppercase border transition-all ${
            !heatmapMode
              ? 'text-accent-cyan border-accent-cyan/30 bg-accent-cyan/10'
              : 'text-text-dim border-surface-border bg-base-800/80'
          }`}
          style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}
        >
          Points
        </button>
        <button
          onClick={() => setHeatmapMode(true)}
          className={`px-2 py-1 text-xxs font-display font-semibold tracking-wider uppercase border transition-all ${
            heatmapMode
              ? 'text-accent-cyan border-accent-cyan/30 bg-accent-cyan/10'
              : 'text-text-dim border-surface-border bg-base-800/80'
          }`}
          style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}
        >
          Density
        </button>
      </div>

      {/* Stats overlay */}
      <div className="absolute bottom-2 left-2 z-[1000]">
        <div className="bg-base-800/80 border border-surface-border px-2 py-1" style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}>
          <span className="data-readout">{entityCount} geocoded entities</span>
        </div>
      </div>

      {/* Empty state */}
      {entityCount === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-[1000] pointer-events-none">
          <div className="text-center bg-base-800/60 px-4 py-3" style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}>
            <div className="data-readout mb-1">No geocoded entities</div>
            <div className="text-xxs text-text-dim">Entities with location data will appear on this map</div>
          </div>
        </div>
      )}
    </div>
  )
}

// Type declaration for dynamic import
declare const L: typeof import('leaflet')
