// ============================================
// TrackingPanel — ADS-B, AIS, and satellite tracking
// Three tabs: MARITIME, AIRCRAFT, SATELLITES
// ============================================

import React, { useState, useCallback } from 'react'
import { useGeoStore } from './geoStore'
import type { TrackingTab, TrackedVessel, TrackedAircraft, TrackedSatellite } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

const TABS: { id: TrackingTab; label: string; icon: string }[] = [
  { id: 'maritime', label: 'MARITIME', icon: '🚢' },
  { id: 'aircraft', label: 'AIRCRAFT', icon: '✈️' },
  { id: 'satellites', label: 'SATELLITES', icon: '🛰' },
]

const authHeaders = () => ({
  Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
  'X-API-Key': localStorage.getItem('periphery_api_key') || '',
})

// ── Maritime Tab ─────────────────────────────────────────────────

const MaritimeView: React.FC = () => {
  const selectedLocation = useGeoStore((s) => s.selectedLocation)
  const trackedVessels = useGeoStore((s) => s.trackedVessels)
  const setTrackedVessels = useGeoStore((s) => s.setTrackedVessels)
  const trackingLoading = useGeoStore((s) => s.trackingLoading)
  const setTrackingLoading = useGeoStore((s) => s.setTrackingLoading)

  const [mmsiSearch, setMmsiSearch] = useState('')
  const [distance, setDistance] = useState(10)

  const scanNearby = useCallback(async () => {
    if (!selectedLocation) return
    setTrackingLoading(true)
    try {
      const resp = await fetch(
        `${API_BASE}/api/geo/tracking/vessels-nearby?lat=${selectedLocation.lat}&lng=${selectedLocation.lng}&distance=${distance}`,
        { headers: authHeaders() }
      )
      if (resp.ok) {
        const data = await resp.json()
        // Normalize response — position-api returns varying shapes
        const vessels: TrackedVessel[] = (data.vessels || data.data || data || []).map(
          (v: Record<string, unknown>) => ({
            mmsi: String(v.mmsi || v.MMSI || ''),
            name: String(v.name || v.shipname || v.SHIPNAME || 'Unknown'),
            lat: Number(v.lat || v.LAT || 0),
            lng: Number(v.lng || v.lon || v.LON || 0),
            speed: Number(v.speed || v.SPEED || 0),
            course: Number(v.course || v.COURSE || 0),
            type: String(v.type || v.shipType || ''),
            flag: String(v.flag || v.FLAG || ''),
            timestamp: String(v.timestamp || v.TIMESTAMP || ''),
          })
        )
        setTrackedVessels(vessels)
      }
    } catch {
      // Position API not available
    } finally {
      setTrackingLoading(false)
    }
  }, [selectedLocation, distance, setTrackedVessels, setTrackingLoading])

  const lookupMmsi = useCallback(async () => {
    if (!mmsiSearch.trim()) return
    setTrackingLoading(true)
    try {
      const resp = await fetch(
        `${API_BASE}/api/geo/tracking/vessel?mmsi=${mmsiSearch.trim()}`,
        { headers: authHeaders() }
      )
      if (resp.ok) {
        const data = await resp.json()
        const v = data.vessel || data.data || data
        if (v) {
          const vessel: TrackedVessel = {
            mmsi: String(v.mmsi || v.MMSI || mmsiSearch),
            name: String(v.name || v.shipname || 'Unknown'),
            lat: Number(v.lat || v.LAT || 0),
            lng: Number(v.lng || v.lon || v.LON || 0),
            speed: Number(v.speed || v.SPEED || 0),
            course: Number(v.course || v.COURSE || 0),
            type: String(v.type || ''),
            flag: String(v.flag || ''),
            timestamp: String(v.timestamp || ''),
          }
          setTrackedVessels([vessel, ...trackedVessels.filter((x) => x.mmsi !== vessel.mmsi)])
        }
      }
    } catch {
      // lookup failed
    } finally {
      setTrackingLoading(false)
    }
  }, [mmsiSearch, trackedVessels, setTrackedVessels, setTrackingLoading])

  return (
    <div className="space-y-3">
      {/* Nearby scan */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={distance}
            onChange={(e) => setDistance(Number(e.target.value))}
            min={1}
            max={500}
            className="w-16 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                       text-xxs font-mono text-text-bright focus:border-accent-cyan/50 outline-none"
          />
          <span className="text-xxs text-text-dim font-mono">NM</span>
          <button
            onClick={scanNearby}
            disabled={!selectedLocation || trackingLoading}
            className="flex-1 text-xxs font-mono text-accent-cyan px-2 py-1
                       border border-accent-cyan/20 rounded hover:bg-accent-cyan/10
                       disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {trackingLoading ? '...' : '📡 SCAN NEARBY'}
          </button>
        </div>

        {/* MMSI lookup */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={mmsiSearch}
            onChange={(e) => setMmsiSearch(e.target.value)}
            placeholder="MMSI number"
            onKeyDown={(e) => e.key === 'Enter' && lookupMmsi()}
            className="flex-1 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                       text-xxs font-mono text-text-bright placeholder:text-text-dim/40
                       focus:border-accent-cyan/50 outline-none"
          />
          <button
            onClick={lookupMmsi}
            disabled={!mmsiSearch.trim() || trackingLoading}
            className="text-xxs font-mono text-accent-cyan px-2 py-1
                       border border-accent-cyan/20 rounded hover:bg-accent-cyan/10
                       disabled:opacity-30 disabled:cursor-not-allowed"
          >
            TRACK
          </button>
        </div>
      </div>

      {/* Results */}
      {trackedVessels.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-xxs text-text-dim font-mono">NO VESSELS TRACKED</p>
          <p className="text-xxs text-text-dim mt-1">Select a location and scan nearby</p>
        </div>
      ) : (
        <div className="space-y-1">
          <p className="text-xxs text-text-dim font-mono">{trackedVessels.length} VESSEL(S)</p>
          {trackedVessels.map((v) => (
            <div
              key={v.mmsi}
              className="bg-black/40 border border-accent-cyan/10 rounded px-3 py-2 space-y-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xxs font-mono text-text-bright">{v.name}</span>
                {v.flag && (
                  <span className="text-xxs font-mono text-text-dim">{v.flag}</span>
                )}
              </div>
              <div className="data-readout grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span className="text-xxs font-mono text-text-dim">MMSI</span>
                <span className="text-xxs font-mono text-accent-cyan">{v.mmsi}</span>
                <span className="text-xxs font-mono text-text-dim">POS</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {v.lat.toFixed(4)}°, {v.lng.toFixed(4)}°
                </span>
                <span className="text-xxs font-mono text-text-dim">SPD/CRS</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {v.speed.toFixed(1)} kn / {v.course.toFixed(0)}°
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Aircraft Tab ─────────────────────────────────────────────────

const AircraftView: React.FC = () => {
  const trackedAircraft = useGeoStore((s) => s.trackedAircraft)
  const setTrackedAircraft = useGeoStore((s) => s.setTrackedAircraft)
  const trackingLoading = useGeoStore((s) => s.trackingLoading)
  const setTrackingLoading = useGeoStore((s) => s.setTrackingLoading)

  const [icaoSearch, setIcaoSearch] = useState('')

  const lookupIcao = useCallback(async () => {
    if (!icaoSearch.trim()) return
    setTrackingLoading(true)
    try {
      const resp = await fetch(
        `${API_BASE}/api/geo/tracking/aircraft?icao=${icaoSearch.trim()}`,
        { headers: authHeaders() }
      )
      if (resp.ok) {
        const data = await resp.json()
        const a = data.aircraft || data.data || data
        if (a) {
          const aircraft: TrackedAircraft = {
            icao: String(a.icao || a.hex || icaoSearch),
            callsign: String(a.callsign || a.flight || 'Unknown').trim(),
            lat: Number(a.lat || 0),
            lng: Number(a.lng || a.lon || 0),
            altitude: Number(a.altitude || a.alt_baro || a.alt || 0),
            speed: Number(a.speed || a.gs || 0),
            heading: Number(a.heading || a.track || 0),
            timestamp: String(a.timestamp || a.seen || ''),
          }
          setTrackedAircraft([
            aircraft,
            ...trackedAircraft.filter((x) => x.icao !== aircraft.icao),
          ])
        }
      }
    } catch {
      // lookup failed
    } finally {
      setTrackingLoading(false)
    }
  }, [icaoSearch, trackedAircraft, setTrackedAircraft, setTrackingLoading])

  return (
    <div className="space-y-3">
      {/* ICAO lookup */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={icaoSearch}
          onChange={(e) => setIcaoSearch(e.target.value.toUpperCase())}
          placeholder="ICAO hex (e.g. A0B1C2)"
          onKeyDown={(e) => e.key === 'Enter' && lookupIcao()}
          className="flex-1 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                     text-xxs font-mono text-text-bright placeholder:text-text-dim/40
                     focus:border-accent-cyan/50 outline-none"
        />
        <button
          onClick={lookupIcao}
          disabled={!icaoSearch.trim() || trackingLoading}
          className="text-xxs font-mono text-accent-cyan px-2 py-1
                     border border-accent-cyan/20 rounded hover:bg-accent-cyan/10
                     disabled:opacity-30 disabled:cursor-not-allowed"
        >
          {trackingLoading ? '...' : 'TRACK'}
        </button>
      </div>

      {/* Results */}
      {trackedAircraft.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-xxs text-text-dim font-mono">NO AIRCRAFT TRACKED</p>
          <p className="text-xxs text-text-dim mt-1">Enter an ICAO hex code to track</p>
        </div>
      ) : (
        <div className="space-y-1">
          <p className="text-xxs text-text-dim font-mono">{trackedAircraft.length} AIRCRAFT</p>
          {trackedAircraft.map((a) => (
            <div
              key={a.icao}
              className="bg-black/40 border border-accent-cyan/10 rounded px-3 py-2 space-y-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xxs font-mono text-text-bright">{a.callsign}</span>
                <span className="text-xxs font-mono text-text-dim">{a.icao}</span>
              </div>
              <div className="data-readout grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span className="text-xxs font-mono text-text-dim">POS</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {a.lat.toFixed(4)}°, {a.lng.toFixed(4)}°
                </span>
                <span className="text-xxs font-mono text-text-dim">ALT</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {a.altitude.toLocaleString()} ft
                </span>
                <span className="text-xxs font-mono text-text-dim">SPD/HDG</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {a.speed.toFixed(0)} kn / {a.heading.toFixed(0)}°
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Satellites Tab ───────────────────────────────────────────────

const SatellitesView: React.FC = () => {
  const selectedLocation = useGeoStore((s) => s.selectedLocation)
  const trackedSatellites = useGeoStore((s) => s.trackedSatellites)
  const setTrackedSatellites = useGeoStore((s) => s.setTrackedSatellites)
  const trackingLoading = useGeoStore((s) => s.trackingLoading)
  const setTrackingLoading = useGeoStore((s) => s.setTrackingLoading)

  const [radius, setRadius] = useState(70)
  const [category, setCategory] = useState(0)

  const scanAbove = useCallback(async () => {
    if (!selectedLocation) return
    setTrackingLoading(true)
    try {
      const resp = await fetch(
        `${API_BASE}/api/geo/tracking/satellites-above?lat=${selectedLocation.lat}&lng=${selectedLocation.lng}&radius=${radius}&category=${category}`,
        { headers: authHeaders() }
      )
      if (resp.ok) {
        const data = await resp.json()
        const sats: TrackedSatellite[] = (data.above || []).map(
          (s: Record<string, unknown>) => ({
            satid: Number(s.satid || 0),
            satname: String(s.satname || 'Unknown'),
            satlat: Number(s.satlat || 0),
            satlng: Number(s.satlng || 0),
            satalt: Number(s.satalt || 0),
            intDesignator: String(s.intDesignator || ''),
            launchDate: String(s.launchDate || ''),
          })
        )
        setTrackedSatellites(sats)
      }
    } catch {
      // N2YO not available
    } finally {
      setTrackingLoading(false)
    }
  }, [selectedLocation, radius, category, setTrackedSatellites, setTrackingLoading])

  const SAT_CATEGORIES = [
    { id: 0, label: 'ALL' },
    { id: 18, label: 'AMATEUR' },
    { id: 52, label: 'ISS' },
    { id: 22, label: 'GPS' },
    { id: 6, label: 'WEATHER' },
    { id: 14, label: 'RADAR' },
  ]

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="space-y-2">
        <div className="flex items-center gap-1 flex-wrap">
          {SAT_CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setCategory(cat.id)}
              className={`px-2 py-0.5 text-xxs font-mono rounded transition-colors
                ${category === cat.id
                  ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                  : 'text-text-dim border border-transparent hover:bg-accent-cyan/5'
                }`}
            >
              {cat.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={radius}
            onChange={(e) => setRadius(Number(e.target.value))}
            min={1}
            max={90}
            className="w-16 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                       text-xxs font-mono text-text-bright focus:border-accent-cyan/50 outline-none"
          />
          <span className="text-xxs text-text-dim font-mono">DEG</span>
          <button
            onClick={scanAbove}
            disabled={!selectedLocation || trackingLoading}
            className="flex-1 text-xxs font-mono text-accent-cyan px-2 py-1
                       border border-accent-cyan/20 rounded hover:bg-accent-cyan/10
                       disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {trackingLoading ? '...' : '📡 SCAN ABOVE'}
          </button>
        </div>
      </div>

      {/* Results */}
      {trackedSatellites.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-xxs text-text-dim font-mono">NO SATELLITES TRACKED</p>
          <p className="text-xxs text-text-dim mt-1">Select a location and scan above</p>
        </div>
      ) : (
        <div className="space-y-1">
          <p className="text-xxs text-text-dim font-mono">
            {trackedSatellites.length} SATELLITE(S) ABOVE
          </p>
          {trackedSatellites.slice(0, 50).map((s) => (
            <div
              key={s.satid}
              className="bg-black/40 border border-accent-cyan/10 rounded px-3 py-2 space-y-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xxs font-mono text-text-bright truncate max-w-[200px]">
                  {s.satname}
                </span>
                <span className="text-xxs font-mono text-text-dim">#{s.satid}</span>
              </div>
              <div className="data-readout grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span className="text-xxs font-mono text-text-dim">POS</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {s.satlat.toFixed(2)}°, {s.satlng.toFixed(2)}°
                </span>
                <span className="text-xxs font-mono text-text-dim">ALT</span>
                <span className="text-xxs font-mono text-accent-cyan">
                  {s.satalt.toFixed(0)} km
                </span>
                {s.launchDate && (
                  <>
                    <span className="text-xxs font-mono text-text-dim">LAUNCH</span>
                    <span className="text-xxs font-mono text-accent-cyan">{s.launchDate}</span>
                  </>
                )}
              </div>
            </div>
          ))}
          {trackedSatellites.length > 50 && (
            <p className="text-xxs text-text-dim font-mono text-center">
              +{trackedSatellites.length - 50} more
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main TrackingPanel ───────────────────────────────────────────

export const TrackingPanel: React.FC = () => {
  const trackingTab = useGeoStore((s) => s.trackingTab)
  const setTrackingTab = useGeoStore((s) => s.setTrackingTab)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/80 border-b border-accent-cyan/20 shrink-0">
        <span className="text-xxs font-mono text-accent-cyan">📡 TRACKING</span>
        <button
          onClick={() => setActivePanel('none')}
          className="text-text-dim hover:text-text-bright text-xs px-1"
        >
          ✕
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-accent-cyan/10 shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setTrackingTab(tab.id)}
            className={`flex-1 px-2 py-1.5 text-xxs font-mono transition-colors
              ${trackingTab === tab.id
                ? 'text-accent-cyan border-b-2 border-accent-cyan bg-accent-cyan/5'
                : 'text-text-dim hover:text-text-bright hover:bg-accent-cyan/5'
              }`}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3">
        {trackingTab === 'maritime' && <MaritimeView />}
        {trackingTab === 'aircraft' && <AircraftView />}
        {trackingTab === 'satellites' && <SatellitesView />}
      </div>
    </div>
  )
}

export default TrackingPanel
