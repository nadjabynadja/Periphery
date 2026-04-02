// ============================================
// GoogleMapView — 2D/3D/Satellite map with entity overlay
// Replaces Mapbox GeographicOverlay
// ============================================

import React, { useRef, useEffect, useCallback, useMemo } from 'react'
import { importLibrary, setOptions } from '@googlemaps/js-api-loader'
import { useStore } from '../../store'
import { useGeoStore } from './geoStore'
import type { EntityNode } from '../../api/types'
import type { GeoLocation } from './types'

const GOOGLE_MAP_KEY = import.meta.env.VITE_GOOGLE_MAP_KEY || ''
const MAP_ID_3D = import.meta.env.VITE_GOOGLE_MAP_ID_3D || ''
const MAP_ID_2D = import.meta.env.VITE_GOOGLE_MAP_ID_2D || ''

let _initialized = false
function ensureLoader() {
  if (!_initialized && GOOGLE_MAP_KEY) {
    setOptions({
      key: GOOGLE_MAP_KEY,
      v: 'beta',
    })
    _initialized = true
  }
}

// Entity type → marker color
const ENTITY_COLORS: Record<string, string> = {
  PERSON: '#00D4FF',
  ORGANIZATION: '#FF6B35',
  LOCATION: '#7CFC00',
  VESSEL: '#FFD700',
  AIRCRAFT: '#FF69B4',
  FINANCIAL: '#00FF7F',
  DOCUMENT: '#9370DB',
  default: '#00D4FF',
}

export const GoogleMapView: React.FC = () => {
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<google.maps.Map | null>(null)
  const markersRef = useRef<google.maps.marker.AdvancedMarkerElement[]>([])
  const selectedMarkerRef = useRef<google.maps.marker.AdvancedMarkerElement | null>(null)
  const drawingManagerRef = useRef<google.maps.drawing.DrawingManager | null>(null)

  const entities = useStore((s) => s.entities)
  const confidenceFloor = useStore((s) => s.confidenceFloor)
  const setSelectedElement = useStore((s) => s.setSelectedElement)

  const mapView = useGeoStore((s) => s.mapView)
  const setMapView = useGeoStore((s) => s.setMapView)
  const selectedLocation = useGeoStore((s) => s.selectedLocation)
  const showEntityMarkers = useGeoStore((s) => s.showEntityMarkers)
  const drawingAOI = useGeoStore((s) => s.drawingAOI)
  const setAoiPolygon = useGeoStore((s) => s.setAoiPolygon)
  const setDrawingAOI = useGeoStore((s) => s.setDrawingAOI)

  // Entities with location
  const locatedEntities = useMemo(
    () =>
      showEntityMarkers
        ? entities.filter((e) => e.location && e.confidence >= confidenceFloor)
        : [],
    [entities, confidenceFloor, showEntityMarkers]
  )

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current || !GOOGLE_MAP_KEY) return

    let cancelled = false

    const init = async () => {
      ensureLoader()
      await importLibrary('maps')
      await importLibrary('marker')

      if (cancelled || !mapContainer.current) return

      const map = new google.maps.Map(mapContainer.current, {
        center: { lat: mapView.center.lat, lng: mapView.center.lng },
        zoom: mapView.zoom,
        tilt: mapView.mode === 'photorealistic3d' ? 45 : 0,
        heading: mapView.heading,
        mapId: mapView.mode === 'photorealistic3d' ? MAP_ID_3D : MAP_ID_2D,
        mapTypeId:
          mapView.mode === 'satellite'
            ? google.maps.MapTypeId.HYBRID
            : google.maps.MapTypeId.ROADMAP,
        disableDefaultUI: true,
        zoomControl: true,
        streetViewControl: false,
        fullscreenControl: false,
        mapTypeControl: false,
        gestureHandling: 'greedy',
        backgroundColor: '#0a0a0a',
        styles:
          mapView.mode !== 'satellite'
            ? [
                { elementType: 'geometry', stylers: [{ color: '#0d1117' }] },
                { elementType: 'labels.text.stroke', stylers: [{ color: '#0d1117' }] },
                { elementType: 'labels.text.fill', stylers: [{ color: '#4a5568' }] },
                {
                  featureType: 'road',
                  elementType: 'geometry',
                  stylers: [{ color: '#1a202c' }],
                },
                {
                  featureType: 'water',
                  elementType: 'geometry',
                  stylers: [{ color: '#0c1929' }],
                },
                {
                  featureType: 'poi',
                  elementType: 'labels',
                  stylers: [{ visibility: 'off' }],
                },
              ]
            : undefined,
      })

      mapRef.current = map

      // Sync map movements back to store
      map.addListener('idle', () => {
        const center = map.getCenter()
        const zoom = map.getZoom()
        if (center && zoom) {
          setMapView({
            center: { lat: center.lat(), lng: center.lng() },
            zoom,
          })
        }
      })

      // Right-click → set location
      map.addListener('rightclick', (e: google.maps.MapMouseEvent) => {
        if (e.latLng) {
          const loc: GeoLocation = {
            lat: e.latLng.lat(),
            lng: e.latLng.lng(),
            name: `${e.latLng.lat().toFixed(5)}, ${e.latLng.lng().toFixed(5)}`,
          }
          useGeoStore.getState().setSelectedLocation(loc)
          useGeoStore.getState().setStreetViewLocation(loc)
        }
      })
    }

    init()

    return () => {
      cancelled = true
      if (mapRef.current) {
        // Clean up markers
        markersRef.current.forEach((m) => (m.map = null))
        markersRef.current = []
        mapRef.current = null
      }
    }
  }, []) // Only init once

  // Update map type/mode when changed
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (mapView.mode === 'satellite') {
      map.setMapTypeId(google.maps.MapTypeId.HYBRID)
      map.setTilt(0)
    } else if (mapView.mode === 'photorealistic3d') {
      map.setTilt(45)
      if (MAP_ID_3D) {
        // 3D tiles require a specific map ID
        map.setMapTypeId(google.maps.MapTypeId.ROADMAP)
      }
    } else {
      map.setMapTypeId(google.maps.MapTypeId.ROADMAP)
      map.setTilt(0)
    }
  }, [mapView.mode])

  // Pan to selected location
  useEffect(() => {
    const map = mapRef.current
    if (!map || !selectedLocation) return

    map.panTo({ lat: selectedLocation.lat, lng: selectedLocation.lng })
    map.setZoom(18)

    // Add/update selected marker
    if (selectedMarkerRef.current) {
      selectedMarkerRef.current.map = null
    }

    const pin = document.createElement('div')
    pin.innerHTML = `
      <div style="
        width: 16px; height: 16px; border-radius: 50%;
        background: #FF3333; border: 2px solid #FFFFFFcc;
        box-shadow: 0 0 12px #FF333388, 0 0 24px #FF333344;
        animation: pulse-marker 1.5s infinite;
      "></div>
    `

    const marker = new google.maps.marker.AdvancedMarkerElement({
      map,
      position: { lat: selectedLocation.lat, lng: selectedLocation.lng },
      content: pin,
      zIndex: 1000,
    })
    selectedMarkerRef.current = marker
  }, [selectedLocation])

  // Update entity markers
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Clear old markers
    markersRef.current.forEach((m) => (m.map = null))
    markersRef.current = []

    for (const entity of locatedEntities) {
      if (!entity.location) continue

      const color = ENTITY_COLORS[entity.entity_type] || ENTITY_COLORS.default
      const size = 6 + entity.rendering.size_multiplier * 4

      const el = document.createElement('div')
      el.style.cssText = `
        width: ${size}px; height: ${size}px;
        border-radius: 50%;
        background: ${color};
        opacity: ${entity.rendering.opacity};
        border: 1px solid ${color}44;
        box-shadow: 0 0 ${entity.rendering.glow_intensity * 8}px ${color}44;
        cursor: pointer;
        transition: transform 0.15s;
      `
      el.addEventListener('mouseenter', () => {
        el.style.transform = 'scale(1.5)'
      })
      el.addEventListener('mouseleave', () => {
        el.style.transform = 'scale(1)'
      })

      const marker = new google.maps.marker.AdvancedMarkerElement({
        map,
        position: { lat: entity.location.lat, lng: entity.location.lon },
        content: el,
        title: `${entity.name} (${entity.entity_type})`,
      })

      marker.addListener('click', () => {
        setSelectedElement({ type: 'entity', id: entity.canonical_id })
      })

      markersRef.current.push(marker)
    }
  }, [locatedEntities, setSelectedElement])

  // Drawing mode for AOI
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (drawingAOI) {
      importLibrary('drawing')
        .then(() => {
          const dm = new google.maps.drawing.DrawingManager({
            drawingMode: google.maps.drawing.OverlayType.POLYGON,
            drawingControl: false,
            polygonOptions: {
              fillColor: '#00D4FF',
              fillOpacity: 0.15,
              strokeColor: '#00D4FF',
              strokeWeight: 2,
              editable: true,
            },
          })
          dm.setMap(map)
          drawingManagerRef.current = dm

          google.maps.event.addListener(dm, 'polygoncomplete', (polygon: google.maps.Polygon) => {
            const path = polygon.getPath()
            const points: GeoLocation[] = []
            for (let i = 0; i < path.getLength(); i++) {
              const pt = path.getAt(i)
              points.push({ lat: pt.lat(), lng: pt.lng() })
            }
            setAoiPolygon(points)
            setDrawingAOI(false)
            dm.setMap(null)
            // Keep polygon on map for visual reference
          })
        })
    } else if (drawingManagerRef.current) {
      drawingManagerRef.current.setMap(null)
      drawingManagerRef.current = null
    }
  }, [drawingAOI, setAoiPolygon, setDrawingAOI])

  if (!GOOGLE_MAP_KEY) {
    return (
      <div className="w-full h-full flex items-center justify-center grid-texture">
        <div className="text-center">
          <p className="data-readout text-text-dim">MAP UNAVAILABLE</p>
          <p className="text-xxs text-text-dim mt-1">
            Set VITE_GOOGLE_MAP_KEY to enable
          </p>
        </div>
      </div>
    )
  }

  return <div ref={mapContainer} className="w-full h-full" />
}

export default GoogleMapView
