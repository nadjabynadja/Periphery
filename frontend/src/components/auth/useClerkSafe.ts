// ============================================
// useClerkSafe — useAuth() that degrades when Clerk isn't mounted
// ============================================
//
// Clerk's useAuth() throws if called outside <ClerkProvider>. We only mount
// the provider when VITE_CLERK_PUBLISHABLE_KEY is set, so components that want
// Clerk state must tolerate its absence. This hook returns a safe shape in
// both cases.
//
// We can't conditionally call a hook, so we read the publishable key (a
// build-time constant — stable across renders) to decide which path to take.
// When the key is absent, useAuth() is never the source of truth.

import { useAuth } from '@clerk/clerk-react'

const clerkConfigured = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY)

export interface SafeAuthState {
  clerkConfigured: boolean
  isLoaded: boolean
  isSignedIn: boolean
  getToken: () => Promise<string | null>
  signOut: () => Promise<void>
}

export function useClerkSafe(): SafeAuthState {
  if (!clerkConfigured) {
    return {
      clerkConfigured: false,
      isLoaded: true,
      isSignedIn: false,
      getToken: async () => null,
      signOut: async () => {},
    }
  }
  // Safe: when configured, ClerkProvider is mounted above this subtree.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { isLoaded, isSignedIn, getToken, signOut } = useAuth()
  return {
    clerkConfigured: true,
    isLoaded,
    isSignedIn: Boolean(isSignedIn),
    getToken: async () => {
      try {
        return await getToken()
      } catch {
        return null
      }
    },
    signOut: async () => {
      await signOut()
    },
  }
}
