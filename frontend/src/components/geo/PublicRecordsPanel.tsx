// ============================================
// PublicRecordsPanel — Property-based public records lookup
// Voters, campaign finance, businesses at an address
// ============================================

import React, { useEffect, useState, useCallback } from 'react'
import { useGeoStore } from './geoStore'
import { peripheryApi } from '../../api/client'
import type { PropertyRecord, VoterRecord, DonorRecord } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function fetchPropertyRecords(
  lat: number,
  lng: number,
  address?: string
): Promise<PropertyRecord | null> {
  try {
    const params = new URLSearchParams()
    params.set('lat', lat.toString())
    params.set('lng', lng.toString())
    if (address) params.set('address', address)

    const resp = await fetch(`${API_BASE}/api/geo/property-records?${params}`, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
        'X-API-Key': localStorage.getItem('periphery_api_key') || '',
      },
    })
    if (!resp.ok) return null
    return await resp.json()
  } catch {
    return null
  }
}

async function runDeepSearch(
  person: string,
  address: string
): Promise<{ results: string[]; loading: boolean }> {
  try {
    const resp = await fetch(`${API_BASE}/api/geo/deep-search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('periphery_session') || ''}`,
        'X-API-Key': localStorage.getItem('periphery_api_key') || '',
      },
      body: JSON.stringify({ person, address }),
    })
    if (!resp.ok) return { results: [], loading: false }
    return await resp.json()
  } catch {
    return { results: [], loading: false }
  }
}

export const PublicRecordsPanel: React.FC = () => {
  const selectedLocation = useGeoStore((s) => s.selectedLocation)
  const activeProperty = useGeoStore((s) => s.activeProperty)
  const setActiveProperty = useGeoStore((s) => s.setActiveProperty)
  const loadingRecords = useGeoStore((s) => s.loadingRecords)
  const setLoadingRecords = useGeoStore((s) => s.setLoadingRecords)
  const setActivePanel = useGeoStore((s) => s.setActivePanel)

  const [deepSearchResults, setDeepSearchResults] = useState<string[]>([])
  const [deepSearching, setDeepSearching] = useState(false)
  const [activeTab, setActiveTab] = useState<'owners' | 'voters' | 'donors' | 'businesses'>(
    'owners'
  )

  // Fetch records when location changes
  useEffect(() => {
    if (!selectedLocation) return

    setLoadingRecords(true)
    fetchPropertyRecords(
      selectedLocation.lat,
      selectedLocation.lng,
      selectedLocation.address
    )
      .then((data) => {
        setActiveProperty(data)
      })
      .finally(() => setLoadingRecords(false))
  }, [selectedLocation?.lat, selectedLocation?.lng])

  const handleDeepSearch = useCallback(
    async (person: string) => {
      if (!selectedLocation?.address) return
      setDeepSearching(true)
      const result = await runDeepSearch(person, selectedLocation.address)
      setDeepSearchResults(result.results)
      setDeepSearching(false)
    },
    [selectedLocation]
  )

  if (!selectedLocation) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <p className="data-readout text-text-dim">NO LOCATION SELECTED</p>
          <p className="text-xxs text-text-dim mt-1">
            Search an address to view records
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/80 border-b border-accent-cyan/20 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xxs font-mono text-accent-cyan">📋 PUBLIC RECORDS</span>
        </div>
        <button
          onClick={() => setActivePanel('none')}
          className="text-text-dim hover:text-text-bright text-xs px-1"
        >
          ✕
        </button>
      </div>

      {/* Address */}
      <div className="px-3 py-2 border-b border-accent-cyan/10 bg-black/40 shrink-0">
        <div className="text-xs text-text-bright font-mono">
          {selectedLocation.address || selectedLocation.name}
        </div>
        <div className="text-xxs text-text-dim mt-0.5">
          {selectedLocation.lat.toFixed(5)}, {selectedLocation.lng.toFixed(5)}
        </div>
      </div>

      {/* Loading */}
      {loadingRecords && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="w-6 h-6 border border-accent-cyan/50 border-t-accent rounded-full animate-spin mx-auto" />
            <p className="text-xxs text-text-dim mt-2 font-mono">
              QUERYING RECORDS...
            </p>
          </div>
        </div>
      )}

      {/* Tabs */}
      {!loadingRecords && (
        <>
          <div className="flex border-b border-accent-cyan/10 shrink-0">
            {(['owners', 'voters', 'donors', 'businesses'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`flex-1 px-2 py-1.5 text-xxs font-mono uppercase transition-colors
                  ${activeTab === tab
                    ? 'text-accent-cyan border-b border-accent bg-accent-cyan/5'
                    : 'text-text-dim hover:text-text-bright'
                  }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {!activeProperty && (
              <div className="text-center py-8">
                <p className="text-xxs text-text-dim font-mono">
                  NO RECORDS FOUND
                </p>
                <p className="text-xxs text-text-dim mt-1">
                  Records API not yet configured for this address
                </p>
              </div>
            )}

            {activeProperty && activeTab === 'owners' && (
              <div className="space-y-2">
                {activeProperty.owners.map((owner, i) => (
                  <div
                    key={i}
                    className="p-2 bg-black/40 border border-accent-cyan/10 rounded"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-text-bright">{owner}</span>
                      <button
                        onClick={() => handleDeepSearch(owner)}
                        disabled={deepSearching}
                        className="text-xxs text-accent-cyan hover:text-accent-cyan/80 font-mono px-2 py-0.5
                                   border border-accent-cyan/20 rounded hover:bg-accent-cyan/10 transition-colors
                                   disabled:opacity-50"
                      >
                        {deepSearching ? '...' : '🔍 DEEP SEARCH'}
                      </button>
                    </div>
                    {activeProperty.parcelId && (
                      <div className="text-xxs text-text-dim mt-1">
                        Parcel: {activeProperty.parcelId}
                      </div>
                    )}
                    {activeProperty.assessedValue && (
                      <div className="text-xxs text-text-dim">
                        Assessed: ${activeProperty.assessedValue.toLocaleString()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {activeProperty && activeTab === 'voters' && (
              <div className="space-y-2">
                {activeProperty.voters.map((v, i) => (
                  <div
                    key={i}
                    className="p-2 bg-black/40 border border-accent-cyan/10 rounded"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-text-bright">{v.name}</span>
                      <span
                        className={`text-xxs font-mono px-1.5 py-0.5 rounded
                        ${v.party === 'DEM'
                          ? 'bg-blue-500/20 text-blue-400'
                          : v.party === 'REP'
                          ? 'bg-red-500/20 text-red-400'
                          : 'bg-gray-500/20 text-gray-400'
                        }`}
                      >
                        {v.party}
                      </span>
                    </div>
                    <div className="flex gap-3 mt-1">
                      <span className="text-xxs text-text-dim">
                        Reg: {v.registrationDate}
                      </span>
                      <span className="text-xxs text-text-dim">
                        Status: {v.status}
                      </span>
                      <span className="text-xxs text-text-dim">
                        Voted: {v.votingHistory}x
                      </span>
                    </div>
                  </div>
                ))}
                {activeProperty.voters.length === 0 && (
                  <p className="text-xxs text-text-dim font-mono text-center py-4">
                    NO VOTER RECORDS AT THIS ADDRESS
                  </p>
                )}
              </div>
            )}

            {activeProperty && activeTab === 'donors' && (
              <div className="space-y-2">
                {activeProperty.donors.map((d, i) => (
                  <div
                    key={i}
                    className="p-2 bg-black/40 border border-accent-cyan/10 rounded"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-text-bright">{d.name}</span>
                      <span className="text-xs text-green-400 font-mono">
                        ${d.totalAmount.toLocaleString()}
                      </span>
                    </div>
                    <div className="flex gap-3 mt-1">
                      <span className="text-xxs text-text-dim">
                        {d.recipientCount} recipients
                      </span>
                      <span className="text-xxs text-text-dim">
                        Last: {d.lastDonation}
                      </span>
                    </div>
                  </div>
                ))}
                {activeProperty.donors.length === 0 && (
                  <p className="text-xxs text-text-dim font-mono text-center py-4">
                    NO DONOR RECORDS AT THIS ADDRESS
                  </p>
                )}
              </div>
            )}

            {activeProperty && activeTab === 'businesses' && (
              <div className="space-y-2">
                {activeProperty.businesses.map((b, i) => (
                  <div
                    key={i}
                    className="p-2 bg-black/40 border border-accent-cyan/10 rounded"
                  >
                    <div className="text-xs text-text-bright">{b.name}</div>
                    <div className="flex gap-3 mt-1">
                      <span className="text-xxs text-text-dim">{b.type}</span>
                      <span
                        className={`text-xxs font-mono
                        ${b.status === 'Active'
                          ? 'text-green-400'
                          : 'text-red-400'
                        }`}
                      >
                        {b.status}
                      </span>
                    </div>
                  </div>
                ))}
                {activeProperty.businesses.length === 0 && (
                  <p className="text-xxs text-text-dim font-mono text-center py-4">
                    NO BUSINESS RECORDS AT THIS ADDRESS
                  </p>
                )}
              </div>
            )}

            {/* Deep search results */}
            {deepSearchResults.length > 0 && (
              <div className="mt-4 p-3 bg-accent-cyan/5 border border-accent-cyan/20 rounded">
                <div className="text-xxs font-mono text-accent-cyan mb-2">
                  🔍 DEEP SEARCH RESULTS
                </div>
                {deepSearchResults.map((r, i) => (
                  <div key={i} className="text-xxs text-text-bright py-1 border-b border-accent-cyan/5 last:border-b-0">
                    {r}
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default PublicRecordsPanel
