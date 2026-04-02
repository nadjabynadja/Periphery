// ============================================
// Geospatial State (Zustand slice)
// ============================================

import { create } from 'zustand'
import type {
  GeoLocation,
  MapViewState,
  SearchResult,
  PropertyRecord,
  CCTVFeed,
  SatelliteImage,
  SatelliteImageRequest,
  GeoPanel,
  TrackingTab,
  TrackedVessel,
  TrackedAircraft,
  TrackedSatellite,
} from './types'

interface GeoState {
  // Map view
  mapView: MapViewState
  setMapView: (v: Partial<MapViewState>) => void

  // Search
  searchResults: SearchResult[]
  setSearchResults: (r: SearchResult[]) => void
  selectedLocation: GeoLocation | null
  setSelectedLocation: (loc: GeoLocation | null) => void
  searchQuery: string
  setSearchQuery: (q: string) => void

  // Active panel
  activePanel: GeoPanel
  setActivePanel: (p: GeoPanel) => void

  // Street View
  streetViewLocation: GeoLocation | null
  setStreetViewLocation: (loc: GeoLocation | null) => void
  streetViewHeading: number
  setStreetViewHeading: (h: number) => void
  streetViewPitch: number
  setStreetViewPitch: (p: number) => void

  // Property / records
  activeProperty: PropertyRecord | null
  setActiveProperty: (p: PropertyRecord | null) => void
  loadingRecords: boolean
  setLoadingRecords: (l: boolean) => void

  // CCTV
  cctvFeeds: CCTVFeed[]
  setCctvFeeds: (feeds: CCTVFeed[]) => void
  activeCctvFeeds: string[] // feed IDs currently displayed
  addCctvFeed: (id: string) => void
  removeCctvFeed: (id: string) => void
  clearCctvFeeds: () => void

  // Satellite
  satelliteImages: SatelliteImage[]
  setSatelliteImages: (imgs: SatelliteImage[]) => void
  satelliteRequest: SatelliteImageRequest | null
  setSatelliteRequest: (req: SatelliteImageRequest | null) => void
  drawingAOI: boolean
  setDrawingAOI: (d: boolean) => void
  aoiPolygon: GeoLocation[]
  setAoiPolygon: (p: GeoLocation[]) => void

  // Tracking
  trackingTab: TrackingTab
  setTrackingTab: (t: TrackingTab) => void
  trackedVessels: TrackedVessel[]
  setTrackedVessels: (v: TrackedVessel[]) => void
  trackedAircraft: TrackedAircraft[]
  setTrackedAircraft: (a: TrackedAircraft[]) => void
  trackedSatellites: TrackedSatellite[]
  setTrackedSatellites: (s: TrackedSatellite[]) => void
  trackingLoading: boolean
  setTrackingLoading: (l: boolean) => void

  // Entity markers from Periphery ontology
  showEntityMarkers: boolean
  setShowEntityMarkers: (s: boolean) => void
}

export const useGeoStore = create<GeoState>((set) => ({
  mapView: {
    center: { lat: 35.7796, lng: -78.6382 }, // Raleigh, NC (default)
    zoom: 10,
    tilt: 45,
    heading: 0,
    mode: 'photorealistic3d',
  },
  setMapView: (v) =>
    set((s) => ({ mapView: { ...s.mapView, ...v } })),

  searchResults: [],
  setSearchResults: (r) => set({ searchResults: r }),
  selectedLocation: null,
  setSelectedLocation: (loc) => set({ selectedLocation: loc }),
  searchQuery: '',
  setSearchQuery: (q) => set({ searchQuery: q }),

  activePanel: 'none',
  setActivePanel: (p) => set({ activePanel: p }),

  streetViewLocation: null,
  setStreetViewLocation: (loc) => set({ streetViewLocation: loc }),
  streetViewHeading: 0,
  setStreetViewHeading: (h) => set({ streetViewHeading: h }),
  streetViewPitch: 0,
  setStreetViewPitch: (p) => set({ streetViewPitch: p }),

  activeProperty: null,
  setActiveProperty: (p) => set({ activeProperty: p }),
  loadingRecords: false,
  setLoadingRecords: (l) => set({ loadingRecords: l }),

  cctvFeeds: [],
  setCctvFeeds: (feeds) => set({ cctvFeeds: feeds }),
  activeCctvFeeds: [],
  addCctvFeed: (id) =>
    set((s) => ({
      activeCctvFeeds: s.activeCctvFeeds.length < 10
        ? [...s.activeCctvFeeds, id]
        : s.activeCctvFeeds,
    })),
  removeCctvFeed: (id) =>
    set((s) => ({
      activeCctvFeeds: s.activeCctvFeeds.filter((f) => f !== id),
    })),
  clearCctvFeeds: () => set({ activeCctvFeeds: [] }),

  satelliteImages: [],
  setSatelliteImages: (imgs) => set({ satelliteImages: imgs }),
  satelliteRequest: null,
  setSatelliteRequest: (req) => set({ satelliteRequest: req }),
  drawingAOI: false,
  setDrawingAOI: (d) => set({ drawingAOI: d }),
  aoiPolygon: [],
  setAoiPolygon: (p) => set({ aoiPolygon: p }),

  trackingTab: 'maritime',
  setTrackingTab: (t) => set({ trackingTab: t }),
  trackedVessels: [],
  setTrackedVessels: (v) => set({ trackedVessels: v }),
  trackedAircraft: [],
  setTrackedAircraft: (a) => set({ trackedAircraft: a }),
  trackedSatellites: [],
  setTrackedSatellites: (s) => set({ trackedSatellites: s }),
  trackingLoading: false,
  setTrackingLoading: (l) => set({ trackingLoading: l }),

  showEntityMarkers: true,
  setShowEntityMarkers: (s) => set({ showEntityMarkers: s }),
}))
