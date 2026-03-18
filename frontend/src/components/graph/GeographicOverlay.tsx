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

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN as string | undefined

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
        source_count: entity.source_count,
        cluster_ids: JSON.stringify(entity.cluster_ids),
        first_seen: entity.first_seen,
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
        size: cluster.size ?? 0,
        key_entities: JSON.stringify(cluster.key_entities),
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

/** Lightweight fingerprint of a FeatureCollection — avoids redundant setData() calls. */
function geoHash(fc: GeoJSON.FeatureCollection): string {
  const f = fc.features
  if (f.length === 0) return '0'
  const first = f[0].geometry
  const last = f[f.length - 1].geometry
  const confSum = f.reduce((s, feat) => s + (feat.properties?.confidence ?? 0), 0)
  return `${f.length}|${JSON.stringify(first)}|${JSON.stringify(last)}|${confSum.toFixed(4)}`
}

export function GeographicOverlay() {
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<mapboxgl.Map | null>(null)
  const popupRef = useRef<mapboxgl.Popup | null>(null)
  const clickPopupRef = useRef<mapboxgl.Popup | null>(null)
  const clickPopupFeatureId = useRef<string | null>(null)
  const sourcesReady = useRef(false)
  const prevEntityHash = useRef('')
  const prevRelHash = useRef('')
  const prevRelDashHash = useRef('')
  const prevClusterHash = useRef('')

  const snapshot = useStore(s => s.snapshot)
  const setSelectedElement = useStore(s => s.setSelectedElement)
  const highlightedEntityIds = useStore(s => s.highlightedEntityIds)

  const [heatmapMode, setHeatmapMode] = useState(false)

  // Build entity position lookup
  const entityPositions = useMemo(() => {
    const map = new Map<string, [number, number]>()
    if (!snapshot) return map
    for (const entity of (snapshot.entities ?? [])) {
      if (entity.location) {
        map.set(entity.canonical_id, [entity.location.lon, entity.location.lat])
      }
    }
    return map
  }, [snapshot])

  // Build GeoJSON data
  const entityGeoJSON = useMemo(
    () => (snapshot ? buildEntityGeoJSON(snapshot.entities ?? [], highlightedEntityIds) : EMPTY_FC),
    [snapshot, highlightedEntityIds],
  )

  const relationshipGeoJSON = useMemo(
    () => (snapshot ? buildRelationshipGeoJSON(snapshot.relationships ?? [], entityPositions, 0.4, Infinity) : EMPTY_FC),
    [snapshot, entityPositions],
  )

  const relationshipDashedGeoJSON = useMemo(
    () => (snapshot ? buildRelationshipGeoJSON(snapshot.relationships ?? [], entityPositions, 0, 0.4) : EMPTY_FC),
    [snapshot, entityPositions],
  )

  const clusterGeoJSON = useMemo(
    () => (snapshot ? buildClusterGeoJSON(snapshot.clusters) : EMPTY_FC),
    [snapshot],
  )

  // Map cluster_id → label for popup display
  const clusterLabels = useMemo(() => {
    const m = new Map<string, string>()
    if (!snapshot) return m
    for (const c of snapshot.clusters) {
      m.set(c.cluster_id, c.label)
    }
    return m
  }, [snapshot])

  // Build entity popup card HTML
  const buildEntityPopupHTML = useCallback((props: Record<string, unknown>) => {
    const conf = props.confidence as number
    const color = confidenceColor(conf)
    const name = props.name as string
    const entityType = props.entity_type as string
    const id = props.id as string
    const sourceCount = props.source_count as number
    const firstSeen = props.first_seen as string
    let clusterIds: string[] = []
    try { clusterIds = JSON.parse(props.cluster_ids as string) } catch { /* empty */ }
    const firstClusterLabel = clusterIds.length > 0 ? clusterLabels.get(clusterIds[0]) : null
    const firstSeenDisplay = firstSeen ? firstSeen.slice(0, 10) : '—'

    return `<div style="font-family:'JetBrains Mono',monospace;max-width:320px;background:#0f1520;border:1px solid #1e293b;border-radius:2px;box-shadow:0 4px 16px rgba(0,0,0,0.5);padding:10px 12px;">
      <div style="font-size:11px;font-weight:600;color:${color};margin-bottom:2px;">&#9679; ${name}</div>
      <div style="font-size:10px;color:#94A3B8;margin-bottom:8px;">${entityType} &middot; ${(conf * 100).toFixed(0)}% confidence</div>
      <div style="border-top:1px solid #1e293b;padding-top:6px;font-size:10px;color:#94A3B8;">
        ${firstClusterLabel ? `<div style="margin-bottom:3px;"><span style="color:#475569;">Cluster:</span> ${firstClusterLabel}</div>` : ''}
        <div style="margin-bottom:3px;"><span style="color:#475569;">Sources:</span> ${sourceCount} documents</div>
        <div><span style="color:#475569;">First seen:</span> ${firstSeenDisplay}</div>
      </div>
      <div style="border-top:1px solid #1e293b;margin-top:8px;padding-top:6px;">
        <a data-entity-id="${id}" style="color:#00D4FF;font-size:10px;cursor:pointer;text-decoration:none;">View Details &rarr;</a>
      </div>
    </div>`
  }, [clusterLabels])

  // Build cluster popup card HTML
  const buildClusterPopupHTML = useCallback((props: Record<string, unknown>) => {
    const conf = props.confidence as number
    const color = confidenceColor(conf)
    const label = props.label as string
    const status = props.status as string
    const id = props.id as string
    const size = props.size as number
    let keyEntities: string[] = []
    try { keyEntities = JSON.parse(props.key_entities as string) } catch { /* empty */ }
    const keyPreview = keyEntities.slice(0, 5).join(', ')

    return `<div style="font-family:'JetBrains Mono',monospace;max-width:320px;background:#0f1520;border:1px solid #1e293b;border-radius:2px;box-shadow:0 4px 16px rgba(0,0,0,0.5);padding:10px 12px;">
      <div style="font-size:11px;font-weight:600;color:${color};margin-bottom:2px;">&#9679; ${label}</div>
      <div style="font-size:10px;color:#94A3B8;margin-bottom:8px;">${status} &middot; ${(conf * 100).toFixed(0)}% coherence</div>
      <div style="border-top:1px solid #1e293b;padding-top:6px;font-size:10px;color:#94A3B8;">
        <div style="margin-bottom:3px;">${size} documents &middot; ${keyEntities.length} entities</div>
        ${keyPreview ? `<div><span style="color:#475569;">Key:</span> ${keyPreview}</div>` : ''}
      </div>
      <div style="border-top:1px solid #1e293b;margin-top:8px;padding-top:6px;">
        <a data-cluster-id="${id}" style="color:#00D4FF;font-size:10px;cursor:pointer;text-decoration:none;">View Details &rarr;</a>
      </div>
    </div>`
  }, [])

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
      console.warn('Periphery: VITE_MAPBOX_ACCESS_TOKEN not set. Map will not render.')
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

    // --- Hover popup (transient) ---
    const popup = new mapboxgl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 10,
    })
    popupRef.current = popup

    // --- Click popup (persistent) ---
    const clickPopup = new mapboxgl.Popup({
      closeButton: true,
      closeOnClick: false,
      offset: 14,
      maxWidth: '340px',
    })
    clickPopupRef.current = clickPopup
    clickPopup.on('close', () => {
      clickPopupFeatureId.current = null
    })

    // Entity hover — skip if click popup is open on same feature
    map.on('mouseenter', LAYERS.circles, (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (!e.features || e.features.length === 0) return
      const props = e.features[0].properties!
      if (clickPopupFeatureId.current === props.id) return
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

    // Entity click — flyTo + click popup + setSelectedElement
    map.on('click', LAYERS.circles, (e) => {
      if (!e.features || e.features.length === 0) return
      e.originalEvent.stopPropagation()
      const props = e.features[0].properties!
      const geom = e.features[0].geometry as GeoJSON.Point
      const id = props.id as string
      const coords = geom.coordinates as [number, number]

      // Fly to entity
      map.flyTo({
        center: coords,
        zoom: Math.max(map.getZoom(), 8),
        duration: 1500,
        essential: true,
      })

      // Remove hover popup, show click popup
      popup.remove()
      clickPopupFeatureId.current = id
      clickPopup.setLngLat(coords)
        .setHTML(buildEntityPopupHTML(props as unknown as Record<string, unknown>))
        .addTo(map)

      // Open detail panel
      useStore.getState().setSelectedElement({ type: 'entity', id })
    })

    // Cluster hover — skip if click popup is open on same feature
    map.on('mouseenter', LAYERS.clusterFill, (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (!e.features || e.features.length === 0) return
      const props = e.features[0].properties!
      if (clickPopupFeatureId.current === props.id) return
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

    // Cluster click — fitBounds + click popup + setSelectedElement
    map.on('click', LAYERS.clusterFill, (e) => {
      if (!e.features || e.features.length === 0) return
      e.originalEvent.stopPropagation()
      const feature = e.features[0]
      const props = feature.properties!
      const id = props.id as string

      // Fit to polygon bounds
      const bounds = new mapboxgl.LngLatBounds()
      const coords = (feature.geometry as GeoJSON.Polygon).coordinates[0]
      coords.forEach(c => bounds.extend(c as [number, number]))
      map.fitBounds(bounds, {
        padding: 80,
        duration: 1500,
        maxZoom: 12,
      })

      // Remove hover popup, show click popup
      popup.remove()
      clickPopupFeatureId.current = id
      clickPopup.setLngLat(e.lngLat)
        .setHTML(buildClusterPopupHTML(props as unknown as Record<string, unknown>))
        .addTo(map)

      // Open detail panel
      useStore.getState().setSelectedElement({ type: 'cluster', id })
    })

    // Click on empty space — close click popup, don't deselect
    map.on('click', (e) => {
      // Only handle if click didn't hit an entity or cluster (those handlers call stopPropagation)
      const entityFeatures = map.queryRenderedFeatures(e.point, { layers: [LAYERS.circles] })
      const clusterFeatures = map.queryRenderedFeatures(e.point, { layers: [LAYERS.clusterFill] })
      if (entityFeatures.length === 0 && clusterFeatures.length === 0) {
        clickPopup.remove()
      }
    })

    // Delegated click listener for "View Details" links in popups
    const handlePopupClick = (ev: MouseEvent) => {
      const target = ev.target as HTMLElement
      const entityId = target.getAttribute('data-entity-id')
      const clusterId = target.getAttribute('data-cluster-id')
      if (entityId) {
        ev.preventDefault()
        useStore.getState().setSelectedElement({ type: 'entity', id: entityId })
      } else if (clusterId) {
        ev.preventDefault()
        useStore.getState().setSelectedElement({ type: 'cluster', id: clusterId })
      }
    }
    mapContainerRef.current?.addEventListener('click', handlePopupClick)

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
    const container = mapContainerRef.current

    return () => {
      container?.removeEventListener('click', handlePopupClick)
      popup.remove()
      clickPopup.remove()
      map.remove()
      mapRef.current = null
      clickPopupRef.current = null
      clickPopupFeatureId.current = null
      sourcesReady.current = false
    }
  }, [buildEntityPopupHTML, buildClusterPopupHTML])

  // Update source data when snapshot / highlights change.
  // Guard each setData() with a fingerprint check so Mapbox only re-renders
  // layers whose geographic content actually changed.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !sourcesReady.current) return

    const updateData = () => {
      const eHash = geoHash(entityGeoJSON)
      if (eHash !== prevEntityHash.current) {
        prevEntityHash.current = eHash
        const src = map.getSource(SOURCES.entities) as mapboxgl.GeoJSONSource | undefined
        if (src) src.setData(entityGeoJSON)
      }

      const rHash = geoHash(relationshipGeoJSON)
      if (rHash !== prevRelHash.current) {
        prevRelHash.current = rHash
        const src = map.getSource(SOURCES.relationships) as mapboxgl.GeoJSONSource | undefined
        if (src) src.setData(relationshipGeoJSON)
      }

      const rdHash = geoHash(relationshipDashedGeoJSON)
      if (rdHash !== prevRelDashHash.current) {
        prevRelDashHash.current = rdHash
        const src = map.getSource(SOURCES.relationshipsDashed) as mapboxgl.GeoJSONSource | undefined
        if (src) src.setData(relationshipDashedGeoJSON)
      }

      const cHash = geoHash(clusterGeoJSON)
      if (cHash !== prevClusterHash.current) {
        prevClusterHash.current = cHash
        const src = map.getSource(SOURCES.clusters) as mapboxgl.GeoJSONSource | undefined
        if (src) src.setData(clusterGeoJSON)
      }
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

  const entityCount = snapshot?.entities?.filter(e => e.location).length || 0

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="absolute inset-0" />

      {!MAPBOX_TOKEN && (
        <div className="absolute inset-0 flex items-center justify-center z-[1000] pointer-events-none">
          <div className="text-center bg-base-800/60 px-4 py-3" style={{ borderRadius: '2px', backdropFilter: 'blur(4px)' }}>
            <div className="data-readout mb-1">Mapbox token missing</div>
            <div className="text-xxs text-text-dim">Set VITE_MAPBOX_ACCESS_TOKEN in frontend/.env</div>
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
