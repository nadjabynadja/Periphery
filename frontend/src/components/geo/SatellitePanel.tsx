// ============================================
// SatellitePanel — SkyFi satellite imagery requests
// Draw AOI, browse collections, request imagery
// ============================================

import React, { useState, useCallback, useEffect } from 'react'
import { useGeoStore } from './geoStore'
import type { SatelliteImage, GeoLocation } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface ArchiveResult {
  id: string
  provider: string
  satellite: string
  captureDate: string
  resolution: number
  cloudCover: number
  cost: number
  thumbnailUrl: string
  areaKm2: number
}

async function searchArchive(
  aoi: GeoLocation[],
  startDate: string,
  endDate: string,
  maxCloud: number
): Promise<ArchiveResult[]> {
  try {
    const resp = await fetch(`${API_BASE}/api/geo/satellite/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
        'X-API-Key': localStorage.getItem('periphery_api_key') || '',
      },
      body: JSON.stringify({
        aoi: aoi.map((p) => [p.lng, p.lat]), // GeoJSON format
        start_date: startDate,
        end_date: endDate,
        max_cloud_cover: maxCloud,
      }),
    })
    if (!resp.ok) return []
    const data = await resp.json()
    return data.results || []
  } catch {
    return []
  }
}

async function requestImagery(
  archiveId: string,
  budget: number
): Promise<{ orderId: string; status: string } | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/geo/satellite/order`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
        'X-API-Key': localStorage.getItem('periphery_api_key') || '',
      },
      body: JSON.stringify({ archive_id: archiveId, budget }),
    })
    if (!resp.ok) return null
    return await resp.json()
  } catch {
    return null
  }
}

export const SatellitePanel: React.FC = () => {
  const aoiPolygon = useGeoStore((s) => s.aoiPolygon)
  const drawingAOI = useGeoStore((s) => s.drawingAOI)
  const setDrawingAOI = useGeoStore((s) => s.setDrawingAOI)
  const setAoiPolygon = useGeoStore((s) => s.setAoiPolygon)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)

  const [startDate, setStartDate] = useState(() => {
    const d = new Date()
    d.setMonth(d.getMonth() - 3)
    return d.toISOString().split('T')[0]
  })
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0])
  const [maxCloud, setMaxCloud] = useState(20)
  const [budget, setBudget] = useState(50)
  const [results, setResults] = useState<ArchiveResult[]>([])
  const [searching, setSearching] = useState(false)
  const [ordering, setOrdering] = useState<string | null>(null)
  const [orderStatus, setOrderStatus] = useState<string | null>(null)
  const [imageType, setImageType] = useState<'free' | 'commercial' | 'tasked'>('free')

  const handleSearch = useCallback(async () => {
    if (aoiPolygon.length < 3) return
    setSearching(true)
    const data = await searchArchive(aoiPolygon, startDate, endDate, maxCloud)
    setResults(data)
    setSearching(false)
  }, [aoiPolygon, startDate, endDate, maxCloud])

  const handleOrder = useCallback(
    async (archiveId: string) => {
      setOrdering(archiveId)
      const result = await requestImagery(archiveId, budget)
      if (result) {
        setOrderStatus(`Order ${result.orderId}: ${result.status}`)
      } else {
        setOrderStatus('Order failed')
      }
      setOrdering(null)
    },
    [budget]
  )

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/80 border-b border-accent-cyan/20 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xxs font-mono text-accent-cyan">🛰 SATELLITE IMAGERY</span>
        </div>
        <button
          onClick={() => setActivePanel('none')}
          className="text-text-dim hover:text-text-bright text-xs px-1"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Step 1: Draw AOI */}
        <div className="space-y-2">
          <div className="text-xxs font-mono text-accent-cyan">1. DEFINE AREA OF INTEREST</div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDrawingAOI(!drawingAOI)}
              className={`px-3 py-1.5 text-xxs font-mono rounded transition-colors
                ${drawingAOI
                  ? 'bg-red-500/20 text-red-400 border border-red-400/30'
                  : 'bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20 hover:bg-accent-cyan/20'
                }`}
            >
              {drawingAOI ? '⬛ CANCEL DRAWING' : '✏️ DRAW ON MAP'}
            </button>
            {aoiPolygon.length > 0 && (
              <>
                <span className="text-xxs text-text-dim font-mono">
                  {aoiPolygon.length} points
                </span>
                <button
                  onClick={() => setAoiPolygon([])}
                  className="text-xxs text-red-400 hover:text-red-300 font-mono"
                >
                  CLEAR
                </button>
              </>
            )}
          </div>
        </div>

        {/* Step 2: Parameters */}
        <div className="space-y-2">
          <div className="text-xxs font-mono text-accent-cyan">2. SEARCH PARAMETERS</div>

          {/* Image type */}
          <div className="flex gap-1">
            {(['free', 'commercial', 'tasked'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setImageType(t)}
                className={`flex-1 px-2 py-1 text-xxs font-mono rounded transition-colors
                  ${imageType === t
                    ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                    : 'text-text-dim border border-accent-cyan/10 hover:bg-accent-cyan/5'
                  }`}
              >
                {t.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Date range */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xxs text-text-dim font-mono">START</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full mt-0.5 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                           text-xxs text-text-bright font-mono focus:outline-none focus:border-accent-cyan/40"
              />
            </div>
            <div>
              <label className="text-xxs text-text-dim font-mono">END</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full mt-0.5 bg-black/60 border border-accent-cyan/20 rounded px-2 py-1
                           text-xxs text-text-bright font-mono focus:outline-none focus:border-accent-cyan/40"
              />
            </div>
          </div>

          {/* Cloud cover */}
          <div>
            <label className="text-xxs text-text-dim font-mono">
              MAX CLOUD COVER: {maxCloud}%
            </label>
            <input
              type="range"
              min={0}
              max={100}
              value={maxCloud}
              onChange={(e) => setMaxCloud(Number(e.target.value))}
              className="w-full mt-0.5 accent-cyan"
            />
          </div>

          {/* Budget (for commercial/tasked) */}
          {imageType !== 'free' && (
            <div>
              <label className="text-xxs text-text-dim font-mono">
                BUDGET: ${budget}
              </label>
              <input
                type="range"
                min={10}
                max={500}
                step={10}
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
                className="w-full mt-0.5 accent-cyan"
              />
            </div>
          )}

          <button
            onClick={handleSearch}
            disabled={aoiPolygon.length < 3 || searching}
            className="w-full px-3 py-2 text-xs font-mono rounded transition-colors
                       bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30 hover:bg-accent-cyan/30
                       disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {searching ? 'SEARCHING...' : '🔍 SEARCH ARCHIVE'}
          </button>
        </div>

        {/* Step 3: Results */}
        {results.length > 0 && (
          <div className="space-y-2">
            <div className="text-xxs font-mono text-accent-cyan">
              3. AVAILABLE IMAGERY ({results.length})
            </div>
            {results.map((r) => (
              <div
                key={r.id}
                className="p-2 bg-black/40 border border-accent-cyan/10 rounded space-y-1"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-bright">{r.satellite}</span>
                  <span className="text-xxs text-green-400 font-mono">
                    {r.cost === 0 ? 'FREE' : `$${r.cost.toFixed(2)}`}
                  </span>
                </div>
                <div className="flex gap-3 text-xxs text-text-dim">
                  <span>{r.captureDate}</span>
                  <span>{r.resolution}m/px</span>
                  <span>☁ {r.cloudCover}%</span>
                  <span>{r.areaKm2.toFixed(1)} km²</span>
                </div>
                {r.thumbnailUrl && (
                  <img
                    src={r.thumbnailUrl}
                    alt={`${r.satellite} capture`}
                    className="w-full h-24 object-cover rounded mt-1 border border-accent-cyan/10"
                  />
                )}
                <button
                  onClick={() => handleOrder(r.id)}
                  disabled={ordering === r.id || (r.cost > budget && imageType !== 'free')}
                  className="w-full mt-1 px-2 py-1 text-xxs font-mono rounded
                             bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20
                             hover:bg-accent-cyan/20 transition-colors
                             disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  {ordering === r.id ? 'REQUESTING...' : 'REQUEST IMAGE'}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Order status */}
        {orderStatus && (
          <div className="p-2 bg-accent-cyan/5 border border-accent-cyan/20 rounded">
            <div className="text-xxs font-mono text-accent-cyan">{orderStatus}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export default SatellitePanel
