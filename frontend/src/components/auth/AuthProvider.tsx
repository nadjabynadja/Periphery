// ============================================
// PERIPHERY — Auth Provider
// Checks session validity on mount, restores user state.
// ============================================

import { useEffect, useState } from 'react'
import { peripheryApi } from '../../api'
import { useStore } from '../../store'

interface AuthProviderProps {
  children: React.ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const sessionToken = useStore(s => s.sessionToken)
  const setAuthUser = useStore(s => s.setAuthUser)
  const setSessionToken = useStore(s => s.setSessionToken)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    async function checkSession() {
      if (!sessionToken) {
        setChecked(true)
        return
      }

      try {
        const me = await peripheryApi.getMe()
        setAuthUser({
          user_id: me.user_id,
          org_id: me.org_id,
          org_name: me.org_name,
          display_name: me.display_name,
          role: me.role,
        })
      } catch {
        // Session invalid — clear it
        setSessionToken(null)
        setAuthUser(null)
      } finally {
        setChecked(true)
      }
    }

    checkSession()
  }, [sessionToken, setAuthUser, setSessionToken])

  if (!checked) {
    return (
      <div className="h-screen w-screen flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <div className="text-sm" style={{ color: 'var(--text-dim)' }}>
          Checking session...
        </div>
      </div>
    )
  }

  return <>{children}</>
}
