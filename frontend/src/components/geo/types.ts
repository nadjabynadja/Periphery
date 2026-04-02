// ============================================
// Geospatial Types
// ============================================

export interface GeoLocation {
  lat: number
  lng: number
  name?: string
  address?: string
  placeId?: string
}

export interface MapViewState {
  center: GeoLocation
  zoom: number
  tilt: number
  heading: number
  mode: 'map' | 'satellite' | 'photorealistic3d'
}

export interface SearchResult {
  placeId: string
  name: string
  address: string
  location: GeoLocation
  types: string[]
  source: 'photon' | 'google'
}

export interface PropertyRecord {
  address: string
  owners: string[]
  voters: VoterRecord[]
  donors: DonorRecord[]
  businesses: BusinessRecord[]
  assessedValue?: number
  parcelId?: string
}

export interface VoterRecord {
  name: string
  party: string
  registrationDate: string
  status: string
  votingHistory: number // elections participated
}

export interface DonorRecord {
  name: string
  totalAmount: number
  recipientCount: number
  lastDonation: string
}

export interface BusinessRecord {
  name: string
  type: string
  status: string
  registeredAgent?: string
}

export interface CCTVFeed {
  id: string
  name: string
  url: string
  location: GeoLocation
  source: string
  status: 'live' | 'offline' | 'unknown'
}

export interface SatelliteImageRequest {
  aoi: GeoLocation[] // polygon vertices
  startDate: string
  endDate: string
  maxCloudCover: number
  resolution?: string
  budget?: number
  status: 'draft' | 'submitted' | 'processing' | 'complete' | 'failed'
}

export interface SatelliteImage {
  id: string
  provider: string
  captureDate: string
  resolution: number // meters per pixel
  cloudCover: number
  cost: number
  thumbnailUrl: string
  fullUrl?: string
}

export type GeoPanel = 'none' | 'streetview' | 'records' | 'cctv' | 'satellite'
