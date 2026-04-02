// ============================================
// AddressSearch — Photon + Google Places hybrid search
// ============================================

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useGeoStore } from './geoStore'
import type { SearchResult, GeoLocation } from './types'

const PHOTON_BASE = 'https://photon.komoot.io/api'
const GOOGLE_PLACES_KEY = import.meta.env.VITE_GOOGLE_PLACES_KEY || ''

// Debounce helper
function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

async function searchPhoton(query: string): Promise<SearchResult[]> {
  try {
    const resp = await fetch(
      `${PHOTON_BASE}?q=${encodeURIComponent(query)}&limit=5&lang=en`
    )
    if (!resp.ok) return []
    const data = await resp.json()
    return (data.features || []).map((f: any) => ({
      placeId: `photon-${f.properties.osm_id || Math.random()}`,
      name: f.properties.name || f.properties.street || '',
      address: [
        f.properties.housenumber,
        f.properties.street,
        f.properties.city,
        f.properties.state,
        f.properties.postcode,
        f.properties.country,
      ]
        .filter(Boolean)
        .join(', '),
      location: {
        lat: f.geometry.coordinates[1],
        lng: f.geometry.coordinates[0],
        name: f.properties.name,
      },
      types: [f.properties.type || 'place'],
      source: 'photon' as const,
    }))
  } catch {
    return []
  }
}

async function searchGooglePlaces(query: string): Promise<SearchResult[]> {
  if (!GOOGLE_PLACES_KEY) return []
  try {
    // Use Places Autocomplete (New) endpoint
    const resp = await fetch(
      'https://places.googleapis.com/v1/places:autocomplete',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Goog-Api-Key': GOOGLE_PLACES_KEY,
        },
        body: JSON.stringify({
          input: query,
          languageCode: 'en',
        }),
      }
    )
    if (!resp.ok) return []
    const data = await resp.json()
    return (data.suggestions || [])
      .filter((s: any) => s.placePrediction)
      .map((s: any) => ({
        placeId: s.placePrediction.placeId,
        name: s.placePrediction.structuredFormat?.mainText?.text || '',
        address: s.placePrediction.text?.text || '',
        location: { lat: 0, lng: 0 }, // Resolved on selection
        types: [],
        source: 'google' as const,
      }))
  } catch {
    return []
  }
}

async function resolveGooglePlace(placeId: string): Promise<GeoLocation | null> {
  if (!GOOGLE_PLACES_KEY) return null
  try {
    const resp = await fetch(
      `https://places.googleapis.com/v1/places/${placeId}?fields=displayName,formattedAddress,location`,
      {
        headers: {
          'X-Goog-Api-Key': GOOGLE_PLACES_KEY,
        },
      }
    )
    if (!resp.ok) return null
    const data = await resp.json()
    return {
      lat: data.location?.latitude || 0,
      lng: data.location?.longitude || 0,
      name: data.displayName?.text || '',
      address: data.formattedAddress || '',
      placeId,
    }
  } catch {
    return null
  }
}

// Parse coordinates from input like "35.7796, -78.6382" or "35.7796 -78.6382"
function parseCoordinates(input: string): GeoLocation | null {
  const match = input.match(/^\s*(-?\d+\.?\d*)[,\s]+(-?\d+\.?\d*)\s*$/)
  if (!match) return null
  const lat = parseFloat(match[1])
  const lng = parseFloat(match[2])
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  return { lat, lng, name: `${lat.toFixed(4)}, ${lng.toFixed(4)}` }
}

export const AddressSearch: React.FC = () => {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const setSelectedLocation = useGeoStore((s) => s.setSelectedLocation)
  const setMapView = useGeoStore((s) => s.setMapView)
  const setStreetViewLocation = useGeoStore((s) => s.setStreetViewLocation)

  const debouncedQuery = useDebounce(query, 300)

  // Search on debounced query change
  useEffect(() => {
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setResults([])
      return
    }

    // Check for raw coordinates first
    const coords = parseCoordinates(debouncedQuery)
    if (coords) {
      setResults([
        {
          placeId: 'coords',
          name: coords.name || 'Coordinates',
          address: `${coords.lat}, ${coords.lng}`,
          location: coords,
          types: ['coordinates'],
          source: 'photon',
        },
      ])
      setOpen(true)
      return
    }

    setLoading(true)
    Promise.all([searchPhoton(debouncedQuery), searchGooglePlaces(debouncedQuery)])
      .then(([photonResults, googleResults]) => {
        // Merge: Google first (better structured), then Photon (free/offline)
        const merged = [...googleResults, ...photonResults]
        // Dedup by approximate location
        const seen = new Set<string>()
        const deduped = merged.filter((r) => {
          const key = `${r.name.toLowerCase().slice(0, 20)}-${r.address.toLowerCase().slice(0, 20)}`
          if (seen.has(key)) return false
          seen.add(key)
          return true
        })
        setResults(deduped.slice(0, 8))
        setOpen(deduped.length > 0)
      })
      .finally(() => setLoading(false))
  }, [debouncedQuery])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleSelect = useCallback(
    async (result: SearchResult) => {
      let location = result.location

      // For Google results, resolve the placeId to get actual coordinates
      if (result.source === 'google' && location.lat === 0 && location.lng === 0) {
        const resolved = await resolveGooglePlace(result.placeId)
        if (resolved) {
          location = resolved
        } else {
          return // Can't resolve
        }
      }

      setSelectedLocation(location)
      setStreetViewLocation(location)
      setMapView({
        center: location,
        zoom: 18,
      })
      setQuery(result.address || result.name)
      setOpen(false)
    },
    [setSelectedLocation, setMapView, setStreetViewLocation]
  )

  return (
    <div className="relative w-full max-w-md">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            if (!e.target.value) setOpen(false)
          }}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Search address, place, or coordinates..."
          className="w-full bg-black/70 border border-accent-cyan/30 rounded px-3 py-1.5
                     text-xs text-text-bright placeholder:text-text-dim
                     focus:outline-none focus:border-accent-cyan/60 backdrop-blur-md
                     font-mono"
        />
        {loading && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            <div className="w-3 h-3 border border-accent-cyan/50 border-t-accent rounded-full animate-spin" />
          </div>
        )}
        {!loading && query && (
          <button
            onClick={() => {
              setQuery('')
              setResults([])
              setOpen(false)
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-dim hover:text-text-bright text-xs"
          >
            ✕
          </button>
        )}
      </div>

      {open && results.length > 0 && (
        <div
          ref={dropdownRef}
          className="absolute top-full left-0 right-0 mt-1 bg-black/90 border border-accent-cyan/20
                     rounded shadow-xl backdrop-blur-md z-50 max-h-80 overflow-y-auto"
        >
          {results.map((r, i) => (
            <button
              key={`${r.placeId}-${i}`}
              onClick={() => handleSelect(r)}
              className="w-full text-left px-3 py-2 hover:bg-accent-cyan/10 border-b border-accent-cyan/5
                         last:border-b-0 transition-colors group"
            >
              <div className="flex items-center gap-2">
                <span className="text-xxs text-accent-cyan/50 font-mono uppercase w-12 shrink-0">
                  {r.source === 'google' ? 'GOOG' : 'PHTN'}
                </span>
                <div className="min-w-0">
                  <div className="text-xs text-text-bright truncate">{r.name}</div>
                  <div className="text-xxs text-text-dim truncate">{r.address}</div>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default AddressSearch
