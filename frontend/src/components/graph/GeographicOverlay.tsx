// ============================================
// GeographicOverlay — Mapbox map visualization
// ============================================

import React, { useRef, useEffect, useMemo } from 'react'
import mapboxgl from 'mapbox-gl'
import { useStore } from '../../store'
import type { EntityNode, DetectedCluster } from '../../api/types'

// Mapbox token from env or placeholder
const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN || import.meta.env.VITE_MAPBOX_TOKEN || ''

export const GeographicOverlay: React.FC = () => {
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<mapboxgl.Map | null>(null)
  const markersRef = useRef<mapboxgl.Marker[]>([])

  const entities = useStore((s) => s.entities)
  const snapshot = useStore((s) => s.snapshot)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const confidenceFloor = useStore((s) => s.confidenceFloor)

  // Entities with location
  const locatedEntities = useMemo(
    () => entities.filter((e) => e.location && e.confidence >= confidenceFloor),
    [entities, confidenceFloor],
  )

  // Cluster geographic centers
  const clusterCenters = useMemo(() => {
    return (snapshot?.clusters || [])
      .filter((c) => c.geographic_center)
      .map((c) => ({
        cluster: c,
        lat: c.geographic_center!.lat,
        lon: c.geographic_center!.lon,
      }))
  }, [snapshot])

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current || !MAPBOX_TOKEN) return

    mapboxgl.accessToken = MAPBOX_TOKEN

    const map = new mapboxgl.Map({
      container: mapContainer.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [0, 20],
      zoom: 2,
      attributionControl: false,
    })

    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right')
    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Update markers
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Clear previous markers
    markersRef.current.forEach((m) => m.remove())
    markersRef.current = []

    // Entity markers
    for (const entity of locatedEntities) {
      if (!entity.location) continue

      const el = document.createElement('div')
      el.className = 'mapbox-entity-marker'
      const opacity = entity.rendering.opacity
      const size = 6 + entity.rendering.size_multiplier * 4
      const color = entity.rendering.glow_color

      el.style.cssText = `
        width: ${size}px; height: ${size}px;
        border-radius: 50%;
        background: ${color};
        opacity: ${opacity};
        border: 1px solid ${color}44;
        box-shadow: 0 0 ${entity.rendering.glow_intensity * 8}px ${color}44;
        cursor: pointer;
      `

      el.addEventListener('click', () => {
        setSelectedElement({ type: 'entity', id: entity.canonical_id })
      })

      const marker = new mapboxgl.Marker({ element: el })
        .setLngLat([entity.location.lon, entity.location.lat])
        .addTo(map)

      markersRef.current.push(marker)
    }

    // Cluster markers
    for (const { cluster, lat, lon } of clusterCenters) {
      const el = document.createElement('div')
      el.className = 'mapbox-cluster-marker'
      const size = 16 + (cluster.size || 0) * 0.5
      el.style.cssText = `
        width: ${Math.min(size, 40)}px; height: ${Math.min(size, 40)}px;
        border-radius: 50%;
        background: #00D4FF11;
        border: 1px dashed #00D4FF44;
        display: flex; align-items: center; justify-content: center;
        cursor: pointer;
        font-size: 8px; font-family: var(--font-mono); color: #00D4FF88;
      `
      el.textContent = String(cluster.size || '?')

      el.addEventListener('click', () => {
        setSelectedElement({ type: 'cluster', id: cluster.cluster_id })
      })

      const marker = new mapboxgl.Marker({ element: el })
        .setLngLat([lon, lat])
        .addTo(map)

      markersRef.current.push(marker)
    }
  }, [locatedEntities, clusterCenters, setSelectedElement])

  if (!MAPBOX_TOKEN) {
    return (
      <div className="w-full h-full flex items-center justify-center grid-texture">
        <div className="text-center">
          <p className="data-readout text-text-dim">MAP UNAVAILABLE</p>
          <p className="text-xxs text-text-dim mt-1">Set VITE_MAPBOX_TOKEN to enable</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full h-full relative">
      <div ref={mapContainer} className="w-full h-full" />
      <div className="absolute bottom-2 left-2 data-readout flex gap-3">
        <span>{locatedEntities.length} located entities</span>
        <span>{clusterCenters.length} cluster regions</span>
      </div>
    </div>
  )
}

export default GeographicOverlay
