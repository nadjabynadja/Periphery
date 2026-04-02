// ============================================
// GeospatialWorkstation — Main geospatial view
// Replaces GeographicOverlay with full OSINT map workstation
// ============================================

import React, { useMemo } from 'react'
import { useGeoStore } from './geoStore'
import { GoogleMapView } from './GoogleMapView'
import { AddressSearch } from './AddressSearch'
import { MapToolbar } from './MapToolbar'
import { StreetViewPanel } from './StreetViewPanel'
import { PublicRecordsPanel } from './PublicRecordsPanel'
import { CCTVPanel } from './CCTVPanel'
import { SatellitePanel } from './SatellitePanel'
import { TrackingPanel } from './TrackingPanel'

const PANEL_WIDTH = 420

export const GeospatialWorkstation: React.FC = () => {
  const activePanel = useGeoStore((s) => s.activePanel)
  const selectedLocation = useGeoStore((s) => s.selectedLocation)

  const panelContent = useMemo(() => {
    switch (activePanel) {
      case 'streetview':
        return <StreetViewPanel />
      case 'records':
        return <PublicRecordsPanel />
      case 'cctv':
        return <CCTVPanel />
      case 'satellite':
        return <SatellitePanel />
      case 'tracking':
        return <TrackingPanel />
      default:
        return null
    }
  }, [activePanel])

  return (
    <div className="w-full h-full flex overflow-hidden">
      {/* Map area */}
      <div className="flex-1 relative">
        {/* Map */}
        <GoogleMapView />

        {/* Overlay: Search + Toolbar */}
        <div className="absolute top-3 left-3 right-3 z-10 flex items-start gap-3">
          <AddressSearch />
          <MapToolbar />
        </div>

        {/* Location info bar */}
        {selectedLocation && (
          <div className="absolute bottom-3 left-3 z-10
                          bg-black/80 backdrop-blur-md border border-accent-cyan/20 rounded
                          px-3 py-1.5 flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
            <div>
              <div className="text-xxs font-mono text-text-bright truncate max-w-[300px]">
                {selectedLocation.name || selectedLocation.address || 'Selected Location'}
              </div>
              <div className="text-xxs font-mono text-text-dim">
                {selectedLocation.lat.toFixed(5)}, {selectedLocation.lng.toFixed(5)}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Side panel */}
      {activePanel !== 'none' && panelContent && (
        <div
          className="border-l border-accent-cyan/20 bg-black/95 backdrop-blur-md shrink-0 overflow-hidden"
          style={{ width: PANEL_WIDTH }}
        >
          {panelContent}
        </div>
      )}
    </div>
  )
}

export default GeospatialWorkstation
