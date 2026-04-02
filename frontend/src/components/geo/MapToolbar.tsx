// ============================================
// MapToolbar — Mode toggle, layer toggles, action buttons
// ============================================

import React from 'react'
import { useGeoStore } from './geoStore'
import type { GeoPanel } from './types'

const MAP_MODES = [
  { id: 'photorealistic3d', label: '3D', icon: '◇' },
  { id: 'map', label: '2D', icon: '◻' },
  { id: 'satellite', label: 'SAT', icon: '◉' },
] as const

const PANELS: { id: GeoPanel; label: string; icon: string }[] = [
  { id: 'streetview', label: 'STREET VIEW', icon: '👁' },
  { id: 'records', label: 'RECORDS', icon: '📋' },
  { id: 'cctv', label: 'CCTV', icon: '📹' },
  { id: 'satellite', label: 'IMAGERY', icon: '🛰' },
  { id: 'tracking', label: 'TRACKING', icon: '📡' },
]

export const MapToolbar: React.FC = () => {
  const mapView = useGeoStore((s) => s.mapView)
  const setMapView = useGeoStore((s) => s.setMapView)
  const activePanel = useGeoStore((s) => s.activePanel)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)
  const showEntityMarkers = useGeoStore((s) => s.showEntityMarkers)
  const setShowEntityMarkers = useGeoStore((s) => s.setShowEntityMarkers)
  const selectedLocation = useGeoStore((s) => s.selectedLocation)

  return (
    <div className="flex items-center gap-2">
      {/* Map mode toggle */}
      <div className="flex bg-black/60 border border-accent-cyan/20 rounded overflow-hidden">
        {MAP_MODES.map((mode) => (
          <button
            key={mode.id}
            onClick={() => setMapView({ mode: mode.id })}
            className={`px-2 py-1 text-xxs font-mono transition-colors
              ${mapView.mode === mode.id
                ? 'bg-accent-cyan/20 text-accent-cyan'
                : 'text-text-dim hover:text-text-bright hover:bg-accent-cyan/5'
              }`}
          >
            {mode.icon} {mode.label}
          </button>
        ))}
      </div>

      {/* Separator */}
      <div className="w-px h-4 bg-accent-cyan/20" />

      {/* Panel buttons */}
      {PANELS.map((panel) => (
        <button
          key={panel.id}
          onClick={() =>
            setActivePanel(activePanel === panel.id ? 'none' : panel.id)
          }
          disabled={
            (panel.id === 'streetview' || panel.id === 'records') &&
            !selectedLocation
          }
          className={`px-2 py-1 text-xxs font-mono rounded transition-colors
            ${activePanel === panel.id
              ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
              : 'text-text-dim hover:text-text-bright hover:bg-accent-cyan/5 border border-transparent'
            }
            disabled:opacity-30 disabled:cursor-not-allowed`}
          title={panel.label}
        >
          {panel.icon}
        </button>
      ))}

      {/* Separator */}
      <div className="w-px h-4 bg-accent-cyan/20" />

      {/* Entity markers toggle */}
      <button
        onClick={() => setShowEntityMarkers(!showEntityMarkers)}
        className={`px-2 py-1 text-xxs font-mono rounded transition-colors
          ${showEntityMarkers
            ? 'text-accent-cyan bg-accent-cyan/10'
            : 'text-text-dim hover:text-text-bright'
          }`}
        title="Toggle entity markers"
      >
        ◆ ENT
      </button>
    </div>
  )
}

export default MapToolbar
