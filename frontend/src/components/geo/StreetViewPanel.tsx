// ============================================
// StreetViewPanel — Google Street View embedded viewer
// ============================================

import React, { useRef, useEffect } from 'react'
import { importLibrary, setOptions } from '@googlemaps/js-api-loader'
import { useGeoStore } from './geoStore'

const GOOGLE_MAP_KEY = import.meta.env.VITE_GOOGLE_MAP_KEY || ''

let _svInit = false
function ensureSVLoader() {
  if (!_svInit && GOOGLE_MAP_KEY) {
    setOptions({ key: GOOGLE_MAP_KEY, v: 'beta' })
    _svInit = true
  }
}

export const StreetViewPanel: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null)
  const panoramaRef = useRef<google.maps.StreetViewPanorama | null>(null)

  const streetViewLocation = useGeoStore((s) => s.streetViewLocation)
  const streetViewHeading = useGeoStore((s) => s.streetViewHeading)
  const streetViewPitch = useGeoStore((s) => s.streetViewPitch)
  const setStreetViewHeading = useGeoStore((s) => s.setStreetViewHeading)
  const setStreetViewPitch = useGeoStore((s) => s.setStreetViewPitch)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)

  useEffect(() => {
    if (!containerRef.current || !streetViewLocation || !GOOGLE_MAP_KEY) return

    let cancelled = false

    const init = async () => {
      ensureSVLoader()
      await importLibrary('streetView')

      if (cancelled || !containerRef.current) return

      const panorama = new google.maps.StreetViewPanorama(containerRef.current, {
        position: {
          lat: streetViewLocation.lat,
          lng: streetViewLocation.lng,
        },
        pov: {
          heading: streetViewHeading,
          pitch: streetViewPitch,
        },
        zoom: 1,
        addressControl: true,
        linksControl: true,
        panControl: true,
        fullscreenControl: false,
        enableCloseButton: false,
        motionTracking: false,
        motionTrackingControl: false,
      })

      panoramaRef.current = panorama

      // Track heading/pitch changes
      panorama.addListener('pov_changed', () => {
        const pov = panorama.getPov()
        setStreetViewHeading(pov.heading)
        setStreetViewPitch(pov.pitch)
      })
    }

    init()

    return () => {
      cancelled = true
      panoramaRef.current = null
    }
  }, [streetViewLocation?.lat, streetViewLocation?.lng])

  // Update position when location changes
  useEffect(() => {
    if (panoramaRef.current && streetViewLocation) {
      panoramaRef.current.setPosition({
        lat: streetViewLocation.lat,
        lng: streetViewLocation.lng,
      })
    }
  }, [streetViewLocation])

  if (!streetViewLocation) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <p className="data-readout text-text-dim">NO LOCATION SELECTED</p>
          <p className="text-xxs text-text-dim mt-1">
            Search an address or right-click the map
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/80 border-b border-accent-cyan/20">
        <div className="flex items-center gap-2">
          <span className="text-xxs font-mono text-accent-cyan">👁 STREET VIEW</span>
          <span className="text-xxs text-text-dim font-mono">
            {streetViewLocation.lat.toFixed(5)}, {streetViewLocation.lng.toFixed(5)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xxs text-text-dim font-mono">
            H:{Math.round(streetViewHeading)}° P:{Math.round(streetViewPitch)}°
          </span>
          <button
            onClick={() => setActivePanel('none')}
            className="text-text-dim hover:text-text-bright text-xs px-1"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Street View container */}
      <div ref={containerRef} className="flex-1" />
    </div>
  )
}

export default StreetViewPanel
