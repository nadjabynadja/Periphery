// ============================================
// AuthProvider — wraps auth context, checks session on mount
// ============================================
//
// Supports two human-auth modes plus machine API keys:
//   - Clerk session (preferred for humans). When a Clerk user is signed in,
//     getMe() carries the Clerk Bearer token (via ClerkAuthBridge) and we
//     populate the store from the backend's resolved identity.
//   - Legacy QR / session token + API key (localStorage), unchanged.
// If Clerk is not configured (no publishable key), useAuth() still works inside
// a no-op and isClerkSignedIn stays false, so the legacy path is used.

import React, { useEffect, useState } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'
import { LoginPage } from './LoginPage'
import { useClerkSafe } from './useClerkSafe'

interface Props {
  children: React.ReactNode
}

export const AuthProvider: React.FC<Props> = ({ children }) => {
  const isAuthenticated = useStore((s) => s.isAuthenticated)
  const setAuthUser = useStore((s) => s.setAuthUser)
  const setSessionToken = useStore((s) => s.setSessionToken)
  const setApiKey = useStore((s) => s.setApiKey)
  const setClassificationScope = useStore((s) => s.setClassificationScope)
  const setAuthRole = useStore((s) => s.setAuthRole)
  const [checking, setChecking] = useState(true)

  // Clerk state. When Clerk isn't configured these stay false/loaded.
  const { isLoaded: clerkLoaded, isSignedIn: clerkSignedIn } = useClerkSafe()

  useEffect(() => {
    // Wait for Clerk to settle so the bridge has registered the token getter
    // before we call getMe() for a Clerk user.
    if (!clerkLoaded) return

    const checkAuth = async () => {
      const token = localStorage.getItem('periphery_session')
      const apiKey = localStorage.getItem('periphery_api_key')

      // Nothing to check: no Clerk session and no stored credentials.
      if (!clerkSignedIn && !token && !apiKey) {
        setChecking(false)
        return
      }

      try {
        // For a Clerk user, getMe() carries the Clerk Bearer token via the
        // ClerkAuthBridge-registered getter; the backend verifies the JWT and
        // returns the resolved identity.
        const me = await peripheryApi.getMe()
        setAuthUser({
          user_id: me.user_id,
          org_id: me.org_id,
          org_name: me.org_name,
          display_name: me.display_name,
          role: me.role,
        })
        setAuthRole(me.role)
        if (me.classification_scope) {
          setClassificationScope(me.classification_scope)
        }
        if (apiKey) {
          setApiKey(apiKey)
        }
      } catch {
        // Clerk users: a failure here just means we fall back to the gate;
        // don't nuke Clerk's own session. Only clear legacy localStorage creds.
        if (!clerkSignedIn) {
          if (token) {
            localStorage.removeItem('periphery_session')
            setSessionToken(null)
          }
          if (apiKey) {
            localStorage.removeItem('periphery_api_key')
            setApiKey(null)
          }
        }
        setAuthUser(null)
      } finally {
        setChecking(false)
      }
    }

    checkAuth()
  }, [
    clerkLoaded,
    clerkSignedIn,
    setAuthUser,
    setSessionToken,
    setApiKey,
    setClassificationScope,
    setAuthRole,
  ])

  if (!clerkLoaded || checking) {
    return (
      <div className="h-screen flex items-center justify-center bg-base-900">
        <div className="text-center">
          <div className="calibrating w-32 mx-auto mb-4" />
          <span className="data-readout">AUTHENTICATING…</span>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginPage />
  }

  return <>{children}</>
}

export default AuthProvider
