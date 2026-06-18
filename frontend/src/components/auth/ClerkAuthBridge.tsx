// ============================================
// ClerkAuthBridge — connects Clerk's session to the API client
// ============================================
//
// Registers a token-getter with the API client so that, whenever a Clerk user
// is signed in, outbound requests carry a fresh Clerk session JWT as
// `Authorization: Bearer <jwt>`. When no Clerk session is active the getter
// returns null and the API client falls back to its existing localStorage
// credentials (API key / legacy session token) — so machine clients and the
// legacy QR login keep working unchanged.
//
// Clerk session tokens are short-lived (~60s); getToken() returns a cached
// token and transparently refreshes it when near expiry, so calling it per
// request is the intended pattern.

import { useEffect } from 'react'
import { useAuth } from '@clerk/clerk-react'
import { setClerkTokenGetter } from '../../api/client'

export const ClerkAuthBridge: React.FC = () => {
  const { getToken, isLoaded, isSignedIn } = useAuth()

  useEffect(() => {
    if (!isLoaded) return

    if (isSignedIn) {
      // Register a getter the API client calls per request.
      // If a custom JWT template is configured for the backend, pass its name:
      //   getToken({ template: 'periphery' })
      setClerkTokenGetter(async () => {
        try {
          return await getToken()
        } catch {
          return null
        }
      })
    } else {
      // No Clerk session — clear the getter so the client falls back to
      // localStorage credentials (API key / legacy session).
      setClerkTokenGetter(null)
    }

    return () => setClerkTokenGetter(null)
  }, [getToken, isLoaded, isSignedIn])

  return null
}

export default ClerkAuthBridge
