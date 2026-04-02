// ============================================
// CCTVPanel — Multi-feed CCTV viewer with detection overlays
// Supports 1-10 concurrent feeds
// ============================================

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useGeoStore } from './geoStore'
import type { CCTVFeed } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

// Detection categories
const DETECTION_CATEGORIES = [
  { id: 'weapons', label: 'Weapons', icon: '🔫', enabled: true },
  { id: 'plates', label: 'License Plates', icon: '🚗', enabled: true },
  { id: 'faces', label: 'Facial Recognition', icon: '👤', enabled: false },
  { id: 'symbols', label: 'Hate Symbols', icon: '⚠️', enabled: true },
  { id: 'gestures', label: 'Gestures', icon: '✋', enabled: false },
  { id: 'lipreading', label: 'Lip Reading', icon: '👄', enabled: false },
] as const

interface DetectionResult {
  feedId: string
  category: string
  confidence: number
  boundingBox: { x: number; y: number; w: number; h: number }
  label: string
  timestamp: string
  matchedWatchlist?: string
}

// Individual feed viewer with detection overlay
const FeedViewer: React.FC<{
  feed: CCTVFeed
  onRemove: () => void
  detections: DetectionResult[]
}> = ({ feed, onRemove, detections }) => {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  // Draw detection bounding boxes
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || detections.length === 0) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)

      for (const det of detections) {
        const color =
          det.category === 'weapons'
            ? '#FF0000'
            : det.category === 'faces'
            ? '#00FF00'
            : det.category === 'plates'
            ? '#FFD700'
            : det.category === 'symbols'
            ? '#FF6600'
            : '#00D4FF'

        // Bounding box
        ctx.strokeStyle = color
        ctx.lineWidth = 2
        ctx.strokeRect(
          det.boundingBox.x * canvas.width,
          det.boundingBox.y * canvas.height,
          det.boundingBox.w * canvas.width,
          det.boundingBox.h * canvas.height
        )

        // Label
        ctx.fillStyle = color
        ctx.font = '10px monospace'
        ctx.fillText(
          `${det.label} ${(det.confidence * 100).toFixed(0)}%`,
          det.boundingBox.x * canvas.width,
          det.boundingBox.y * canvas.height - 4
        )

        // Watchlist match
        if (det.matchedWatchlist) {
          ctx.fillStyle = '#FF0000'
          ctx.font = 'bold 11px monospace'
          ctx.fillText(
            `⚠ ${det.matchedWatchlist}`,
            det.boundingBox.x * canvas.width,
            (det.boundingBox.y + det.boundingBox.h) * canvas.height + 14
          )
        }
      }
    }

    draw()
  }, [detections])

  return (
    <div className="relative bg-black border border-accent-cyan/20 rounded overflow-hidden group">
      {/* Feed header */}
      <div className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between
                      px-2 py-1 bg-gradient-to-b from-black/80 to-transparent">
        <div className="flex items-center gap-1.5">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              feed.status === 'live' ? 'bg-green-400 animate-pulse' : 'bg-red-400'
            }`}
          />
          <span className="text-xxs font-mono text-text-bright truncate max-w-[120px]">
            {feed.name}
          </span>
        </div>
        <button
          onClick={onRemove}
          className="text-text-dim hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity"
        >
          ✕
        </button>
      </div>

      {/* Video feed */}
      <div className="relative aspect-video bg-black">
        {feed.url ? (
          <>
            <video
              ref={videoRef}
              src={feed.url}
              autoPlay
              muted
              playsInline
              className="w-full h-full object-cover"
            />
            <canvas
              ref={canvasRef}
              width={640}
              height={360}
              className="absolute inset-0 w-full h-full pointer-events-none"
            />
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <div className="text-center">
              <p className="text-xxs text-text-dim font-mono">NO FEED</p>
              <p className="text-xxs text-text-dim">{feed.source}</p>
            </div>
          </div>
        )}
      </div>

      {/* Detection alerts */}
      {detections.length > 0 && (
        <div className="absolute bottom-0 left-0 right-0 bg-black/80 px-2 py-1">
          <div className="flex items-center gap-2 overflow-x-auto">
            {detections.slice(0, 3).map((d, i) => (
              <span
                key={i}
                className={`text-xxs font-mono px-1.5 py-0.5 rounded whitespace-nowrap
                  ${d.category === 'weapons'
                    ? 'bg-red-500/20 text-red-400'
                    : d.matchedWatchlist
                    ? 'bg-red-500/20 text-red-400 animate-pulse'
                    : 'bg-accent-cyan/20 text-accent-cyan'
                  }`}
              >
                {d.label}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export const CCTVPanel: React.FC = () => {
  const cctvFeeds = useGeoStore((s) => s.cctvFeeds)
  const activeCctvFeeds = useGeoStore((s) => s.activeCctvFeeds)
  const addCctvFeed = useGeoStore((s) => s.addCctvFeed)
  const removeCctvFeed = useGeoStore((s) => s.removeCctvFeed)
  const clearCctvFeeds = useGeoStore((s) => s.clearCctvFeeds)
  const selectedLocation = useGeoStore((s) => s.selectedLocation)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)

  const [detections, setDetections] = useState<DetectionResult[]>([])
  const [enabledDetections, setEnabledDetections] = useState<Set<string>>(
    new Set(DETECTION_CATEGORIES.filter((c) => c.enabled).map((c) => c.id))
  )
  const [searchingFeeds, setSearchingFeeds] = useState(false)

  // Search for nearby CCTV when location changes
  const searchNearbyFeeds = useCallback(async () => {
    if (!selectedLocation) return
    setSearchingFeeds(true)
    try {
      const resp = await fetch(
        `${API_BASE}/api/geo/cctv/nearby?lat=${selectedLocation.lat}&lng=${selectedLocation.lng}&radius=2000`,
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
            'X-API-Key': localStorage.getItem('periphery_api_key') || '',
          },
        }
      )
      if (resp.ok) {
        const data = await resp.json()
        useGeoStore.getState().setCctvFeeds(data.feeds || [])
      }
    } catch {
      // CCTV search not available yet
    } finally {
      setSearchingFeeds(false)
    }
  }, [selectedLocation])

  const toggleDetection = useCallback((id: string) => {
    setEnabledDetections((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Grid layout based on active feed count
  const gridClass =
    activeCctvFeeds.length <= 1
      ? 'grid-cols-1'
      : activeCctvFeeds.length <= 4
      ? 'grid-cols-2'
      : activeCctvFeeds.length <= 6
      ? 'grid-cols-3'
      : 'grid-cols-4'

  const activeFeeds = cctvFeeds.filter((f) => activeCctvFeeds.includes(f.id))

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/80 border-b border-accent-cyan/20 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xxs font-mono text-accent-cyan">📹 CCTV MONITOR</span>
          <span className="text-xxs text-text-dim font-mono">
            {activeCctvFeeds.length}/10 feeds
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={searchNearbyFeeds}
            disabled={!selectedLocation || searchingFeeds}
            className="text-xxs text-accent-cyan hover:text-accent-cyan/80 font-mono px-2 py-0.5
                       border border-accent-cyan/20 rounded hover:bg-accent-cyan/10
                       disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {searchingFeeds ? '...' : '📡 SCAN NEARBY'}
          </button>
          {activeCctvFeeds.length > 0 && (
            <button
              onClick={clearCctvFeeds}
              className="text-xxs text-red-400 hover:text-red-300 font-mono px-2 py-0.5
                         border border-red-400/20 rounded hover:bg-red-400/10"
            >
              CLEAR ALL
            </button>
          )}
          <button
            onClick={() => setActivePanel('none')}
            className="text-text-dim hover:text-text-bright text-xs px-1"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Detection toggles */}
      <div className="flex items-center gap-1 px-3 py-1.5 bg-black/40 border-b border-accent-cyan/10 shrink-0 overflow-x-auto">
        {DETECTION_CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            onClick={() => toggleDetection(cat.id)}
            className={`px-2 py-0.5 text-xxs font-mono rounded whitespace-nowrap transition-colors
              ${enabledDetections.has(cat.id)
                ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                : 'text-text-dim border border-transparent hover:bg-accent-cyan/5'
              }`}
          >
            {cat.icon} {cat.label}
          </button>
        ))}
      </div>

      {/* Feed grid */}
      <div className="flex-1 overflow-y-auto p-2">
        {activeFeeds.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <p className="text-xxs text-text-dim font-mono">NO ACTIVE FEEDS</p>
              <p className="text-xxs text-text-dim mt-1">
                Scan for nearby cameras or add feeds manually
              </p>
              {cctvFeeds.length > 0 && (
                <div className="mt-4 space-y-1">
                  <p className="text-xxs text-accent-cyan font-mono">
                    {cctvFeeds.length} FEEDS AVAILABLE:
                  </p>
                  {cctvFeeds.slice(0, 5).map((feed) => (
                    <button
                      key={feed.id}
                      onClick={() => addCctvFeed(feed.id)}
                      className="block w-full text-left px-3 py-1.5 text-xxs
                                 bg-black/40 border border-accent-cyan/10 rounded
                                 hover:bg-accent-cyan/10 transition-colors"
                    >
                      <span className="text-text-bright">{feed.name}</span>
                      <span className="text-text-dim ml-2">{feed.source}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className={`grid ${gridClass} gap-2`}>
            {activeFeeds.map((feed) => (
              <FeedViewer
                key={feed.id}
                feed={feed}
                onRemove={() => removeCctvFeed(feed.id)}
                detections={detections.filter((d) => d.feedId === feed.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default CCTVPanel
