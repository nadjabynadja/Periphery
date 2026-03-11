import { useRef, useEffect, useState, useMemo, useCallback } from 'react'
import { useStore } from '../../store'
import mapboxgl from 'mapbox-gl'
import 'mapbox-gl/dist/mapbox-gl.css'
import type { EntityNode, Relationship, DetectedCluster } from '../../api/types'

// Override Mapbox default popup styles to match Periphery theme
const POPUP_STYLE = `
.mapboxgl-popup-content {
  background: #0f1520 !important;
  border: 1px solid #1e293b !important;
  border-radius: 2px !important;
  padding: 6px 8px !important;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4) !important;
  font-family: 'JetBrains Mono', monospace !important;
}
.mapboxgl-popup-tip {
  border-top-color: #1e293b !important;
  border-bottom-color: #1e293b !important;
}
.mapboxgl-popup-close-button {
  color: #94A3B8 !important;
  font-size: 14px !important;
}
.mapboxgl-ctrl-logo { display: none !important; }
`

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined

// Source and layer IDs
const SOURCES = {
  entities: 'entities-source',
  relationships: 'relationships-source',
  relationshipsDashed: 'relationships-dashed-source',
  clusters: 'clusters-source',
} as const

const LAYERS = {
  heatmap: 'entities-heatmap',
  circles: 'entities-circles',
  circleStroke: 'entities-circle-stroke',
  lines: 'relationships-lines',
  linesDashed: 'relationships-lines-dashed',
  clusterFill: 'clusters-fill',
  clusterBorder: 'clusters-border',
} as const

function confidenceColor(confidence: number): string {
  if (confidence >= 0.6) return '#00D4FF'
  if (confidence >= 0.4) return '#FFB833'
  return '#3A4A5C'
}

function buildEntityGeoJSON(
  entities: EntityNode[],
  highlightedEntityIds: Set<string>,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []
  for (const entity of entities) {
    if (!entity.location) continue
    features.push({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: [entity.location.lon, entity.location.lat],
      },
      properties: {
        id: entity.canonical_id,
        name: entity.name,
        entity_type: entity.entity_type,
        confidence: entity.confidence,
        isHighlighted: highlightedEntityIds.size === 0 || highlightedEntityIds.has(entity.canonical_id) ? 1 : 0,
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

function buildRelationshipGeoJSON(
  relationships: Relationship[],
  entityPositions: Map<string, [number, number]>,
  minConfidence: number,
  maxConfidence: number,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []
  for (const rel of relationships) {
    const source = entityPositions.get(rel.subject_id)
    const target = entityPositions.get(rel.object_id)
    if (!source || !target) continue
    if (rel.confidence < minConfidence || rel.confidence >= maxConfidence) continue
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [source, target],
      },
      properties: {
        predicate: rel.predicate,
        confidence: rel.confidence,
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

function buildClusterGeoJSON(clusters: DetectedCluster[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []
  for (const cluster of clusters) {
    if (!cluster.geographic_footprint || cluster.geographic_footprint.length < 3) continue
    const coords = cluster.geographic_footprint.map(p => [p.lon, p.lat] as [number, number])
    // Close the polygon ring
    if (coords.length > 0) {
      const first = coords[0]
      const last = coords[coords.length - 1]
      if (first[0] !== last[0] || first[1] !== last[1]) {
        coords.push([...first] as [number, number])
      }
    }
    features.push({
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [coords],
      },
      properties: {
        id: cluster.cluster_id,
        label: cluster.label,
        status: cluster.status,
        confidence: cluster.confidence,
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

export function GeographicOverlay() {
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<mapboxgl.Map | null>(null)
  const popupRef = useRef<mapboxgl.Popup | null>(null)
  const sourcesReady = useRef(false)

  const snapshot = useStore(s => s.snapshot)
  const setSelectedElement = useStore(s => s.setSelectedElement)
  const highlightedEntityIds = useStore(s => s.highlightedEntityIds)

  const [heatmapMode, setHeatmapMode] = useState(false)

  // Build entity position lookup
  const entityPositions = useMemo(() => {
    const map = new Map<string, [number, number]>()
    if (!snapshot) return map
    for (const entity of snapshot.entities) {
      if (entity.location) {
        map.set(entity.canonical_id, [entity.location.lon, entity.location.lat])
      }
    }
    return map
  }, [snapshot])

  // Build GeoJSON data
  const entityGeoJSON = useMemo(
    () => (snapshot ? buildEntityGeoJSON(snapshot.entities, highlightedEntityIds) : EMPTY_FC),
    [snapshot, highlightedEntityIds],
  )

  const relationshipGeoJSON = useMemo(
    () => (snapshot ? buildRelationshipGeoJSON(snapshot.relationships, entityPositions, 0.4, Infinity) : EMPTY_FC),
    [snapshot, entityPositions],
  )

  const relationshipDashedGeoJSON = useMemo(
    () => (snapshot ? buildRelationshipGeoJSON(snapshot.relationships, entityPositions, 0, 0.4) : EMPTY_FC),
    [snapshot, entityPositions],
  )

  const clusterGeoJSON = useMemo(
    () => (snapshot ? buildClusterGeoJSON(snapshot.clusters) : EMPTY_FC),
    [snapshot],
  )

  // Inject popup styles
  useEffect(() => {
    const style = document.createElement('style')
    style.textContent = POPUP_STYLE
    document.head.appendChild(style)
    return () => { document.head.removeChild(style) }
  }, [])

  // Initialize map
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return
    if (!MAPBOX_TOKEN) {
      console.warn('Periphery: VITE_MAPBOX_TOKEN not set. Map will not render.')
      return
    }

    mapboxgl.accessToken = MAPBOX_TOKEN

    const map = new mapboxgl.Map({
      container: mapContainerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [0, 20],
      zoom: 2,
      attributionControl: false,
      projection: 'globe',
    })

    map.addControl(new mapboxgl.AttributionControl({ compact: true }), 'bottom-right')
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right')

    map.on('load', () => {
      // Atmosphere / fog for globe effect
      map.setFog({
        color: '#0a0e17',
        'high-color': '#0a0e17',
        'horizon-blend': 0.02,
        'space-color': '#0a0e17',
        'star-intensity': 0.2,
      })

      // --- Sources ---
      map.addSource(SOURCES.entities, { type: 'geojson', data: EMPTY_FC })
      map.addSource(SOURCES.relationships, { type: 'geojson', data: EMPTY_FC })
      map.addSource(SOURCES.relationshipsDashed, { type: 'geojson', data: EMPTY_FC })
      map.addSource(SOURCES.clusters, { type: 'geojson', data: EMPTY_FC })

      // --- Cluster fill layer ---
      map.addLayer({
        id: LAYERS.clusterFill,
        type: 'fill',
        source: SOURCES.clusters,
        paint: {
          'fill-color': ['step', ['get', 'confidence'], '#3A4A5C', 0.4, '#FFB833', 0.6, '#00D4FF'],
          'fill-opacity': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.03, 0.6, 0.08, 1, 0.12],
        },
      })

      // --- Cluster border layer (high confidence only) ---
      map.addLayer({
        id: LAYERS.clusterBorder,
        type: 'line',
        source: SOURCES.clusters,
        filter: ['>=', ['get', 'confidence'], 0.6],
        paint: {
          'line-color': ['step', ['get', 'confidence'], '#3A4A5C', 0.4, '#FFB833', 0.6, '#00D4FF'],
          'line-width': 1,
          'line-opacity': 0.6,
        },
      })

      // --- Relationship lines (solid, confidence >= 0.4) ---
      map.addLayer({
        id: LAYERS.lines,
        type: 'line',
        source: SOURCES.relationships,
        paint: {
          'line-color': ['step', ['get', 'confidence'], '#3A4A5C', 0.4, '#FFB833', 0.6, '#00D4FF'],
          'line-width': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.5, 1, 2],
          'line-opacity': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.1, 1, 0.5],
        },
      })

      // --- Relationship lines (dashed, confidence < 0.4) ---
      map.addLayer({
        id: LAYERS.linesDashed,
        type: 'line',
        source: SOURCES.relationshipsDashed,
        paint: {
          'line-color': '#3A4A5C',
          'line-width': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.5, 1, 2],
          'line-opacity': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.1, 1, 0.5],
          'line-dasharray': [4, 4],
        },
      })

      // --- Heatmap layer ---
      map.addLayer({
        id: LAYERS.heatmap,
        type: 'heatmap',
        source: SOURCES.entities,
        layout: { visibility: 'none' },
        paint: {
          'heatmap-weight': ['get', 'confidence'],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 9, 3],
          'heatmap-color': [
            'interpolate',
            ['linear'],
            ['heatmap-density'],
            0, 'rgba(0,0,0,0)',
            0.1, '#0a0e17',
            0.3, '#003344',
            0.5, '#006680',
            0.7, '#00D4FF',
            1, '#FFB833',
          ],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 10, 9, 30],
          'heatmap-opacity': 0.7,
        },
      })

      // --- Entity circles ---
      map.addLayer({
        id: LAYERS.circles,
        type: 'circle',
        source: SOURCES.entities,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['get', 'confidence'], 0, 4, 1, 10],
          'circle-color': ['step', ['get', 'confidence'], '#3A4A5C', 0.4, '#FFB833', 0.6, '#00D4FF'],
          'circle-opacity': [
            'case',
            ['==', ['get', 'isHighlighted'], 1],
            ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.3, 1, 1],
            0.1,
          ],
          'circle-stroke-width': ['case', ['>=', ['get', 'confidence'], 0.6], 1, 0],
          'circle-stroke-color': ['step', ['get', 'confidence'], '#3A4A5C', 0.4, '#FFB833', 0.6, '#00D4FF'],
          'circle-stroke-opacity': [
            'case',
            ['==', ['get', 'isHighlighted'], 1],
            1,
            0.1,
          ],
        },
      })

      sourcesReady.current = true
    })

    // --- Popup for hover ---
    const popup = new mapboxgl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 10,
    })
    popupRef.current = popup

    // Entity hover
    map.on('mouseenter', LAYERS.circles, (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (!e.features || e.features.length === 0) return
      const props = e.features[0].properties!
      const geom = e.features[0].geometry as GeoJSON.Point
      const conf = props.confidence as number
      const color = confidenceColor(conf)
      popup.setLngLat(geom.coordinates as [number, number])
        .setHTML(
          `<div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #E2E8F0;">
            <div style="color: ${color}; font-weight: 500;">${props.name}</div>
            <div style="color: #94A3B8; font-size: 10px;">${props.entity_type} &middot; ${(conf * 100).toFixed(0)}%</div>
          </div>`)
        .addTo(map)
    })
    map.on('mouseleave', LAYERS.circles, () => {
      map.getCanvas().style.cursor = ''
      popup.remove()
    })

    // Entity click
    map.on('click', LAYERS.circles, (e) => {
      if (!e.features || e.features.length === 0) return
      const id = e.features[0].properties!.id as string
      useStore.getState().setSelectedElement({ type: 'entity', id })
    })

    // Cluster hover
    map.on('mouseenter', LAYERS.clusterFill, (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (!e.features || e.features.length === 0) return
      const props = e.features[0].properties!
      const conf = props.confidence as number
      const color = confidenceColor(conf)
      popup.setLngLat(e.lngLat)
        .setHTML(
          `<div style="font-family: 'JetBrains Mono', monospace; font-size: 11px;">
            <div style="color: ${color};">${props.label}</div>
            <div style="color: #94A3B8; font-size: 10px;">${props.status} &middot; ${(conf * 100).toFixed(0)}%</div>
          </div>`)
        .addTo(map)
    })
    map.on('mouseleave', LAYERS.clusterFill, () => {
      map.getCanvas().style.cursor = ''
      popup.remove()
    })

    // Cluster click
    map.on('click', LAYERS.clusterFill, (e) => {
      if (!e.features || e.features.length === 0) return
      const id = e.features[0].properties!.id as string
      useStore.getState().setSelectedElement({ type: 'cluster', id })
    })

    // Relationship hover
    const relHover = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.GeoJSONFeature[] }) => {
      if (!e.features || e.features.length === 0) return
      const props = e.features[0].properties!
      const conf = props.confidence as number
      popup.setLngLat(e.lngLat)
        .setHTML(
          `<div style="font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #94A3B8;">
            ${props.predicate} (${(conf * 100).toFixed(0)}%)
          </div>`)
        .addTo(map)
    }
    const relLeave = () => { popup.remove() }
    map.on('mouseenter', LAYERS.lines, relHover)
    map.on('mouseleave', LAYERS.lines, relLeave)
    map.on('mouseenter', LAYERS.linesDashed, relHover)
    map.on('mouseleave', LAYERS.linesDashed, relLeave)

    mapRef.current = map

    return () => {
      popup.remove()
      map.remove()
      mapRef.current = null
      sourcesReady.current = false
    }
  }, [])

  // Update source data when snapshot / highlights change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !sourcesReady.current) return

    const updateData = () => {
      const entSrc = map.getSource(SOURCES.entities) as mapboxgl.GeoJSONSource | undefined
      const relSrc = map.getSource(SOURCES.relationships) as mapboxgl.GeoJSONSource | undefined
      const relDashSrc = map.getSource(SOURCES.relationshipsDashed) as mapboxgl.GeoJSONSource | undefined
      const cluSrc = map.getSource(SOURCES.clusters) as mapboxgl.GeoJSONSource | undefined

      if (entSrc) entSrc.setData(entityGeoJSON)
      if (relSrc) relSrc.setData(relationshipGeoJSON)
      if (relDashSrc) relDashSrc.setData(relationshipDashedGeoJSON)
      if (cluSrc) cluSrc.setData(clusterGeoJSON)
    }

    if (map.isStyleLoaded()) {
      updateData()
    } else {
      map.once('load', updateData)
    }
  }, [entityGeoJSON, relationshipGeoJSON, relationshipDashedGeoJSON, clusterGeoJSON])

  // Toggle heatmap vs circles mode
  useEffect(() => {
    const map = mapRef.current
    if (!map || !sourcesReady.current) return

    const toggle = () => {
      if (map.getLayer(LAYERS.heatmap)) {
        map.setLayoutProperty(LAYERS.heatmap, 'visibility', heatmapMode ? 'visible' : 'none')
      }
      if (map.getLayer(LAYERS.circles)) {
        map.setLayoutProperty(LAYERS.circles, 'visibility', heatmapMode ? 'none' : 'visible')
      }
      if (map.getLayer(LAYERS.circleStroke)) {
        map.setLayoutProperty(LAYERS.circleStroke, 'visibility', heatmapMode ? 'none' : 'visible')
      }
      // Hide relationship lines in heatmap mode
      if (map.getLayer(LAYERS.lines)) {
        map.setLayoutProperty(LAYERS.lines, 'visibility', heatmapMode ? 'none' : 'visible')
      }
      if (map.getLayer(LAYERS.linesDashed)) {
        map.setLayoutProperty(LAYERS.linesDashed, 'visibility', heatmapMode ? 'none' : 'visible')
      }
    }

    if (map.isStyleLoaded()) {
      toggle()
    } else {
      map.once('load', toggle)
    }
  }, [heatmapMode])

  const entityCount = snapshot?.entities.filter(e => e.location).length || 0

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="absolute inset-0" />

      {!MAPBOX_TOKEN && (
        <div className="absolute inset-0 flex items-center justify-center z-[1000] pointer-events-none">
          <div className="text-center bg-base-800/60 px-4 py-3" style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}>
            <div className="data-readout mb-1">Mapbox token missing</div>
            <div className="text-xxs text-text-dim">Set VITE_MAPBOX_TOKEN in frontend/.env</div>
          </div>
        </div>
      )}

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
      {entityCount === 0 && MAPBOX_TOKEN && (
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
