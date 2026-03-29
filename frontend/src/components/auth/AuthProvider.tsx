// ============================================
// AuthProvider — wraps auth context, checks session on mount
// ============================================

import React, { useEffect, useState } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'
import type { DataClassification } from '../../api/types'
import { LoginPage } from './LoginPage'

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

  useEffect(() => {
    const checkAuth = async () => {
      const token = localStorage.getItem('periphery_session')
      const apiKey = localStorage.getItem('periphery_api_key')

      if (!token && !apiKey) {
        setChecking(false)
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
        setAuthRole(me.role)
        if (me.classification_scope) {
          setClassificationScope(me.classification_scope)
        }
        if (apiKey) {
          setApiKey(apiKey)
        }
      } catch {
        // Session expired or invalid
        if (token) {
          localStorage.removeItem('periphery_session')
          setSessionToken(null)
        }
        if (apiKey) {
          localStorage.removeItem('periphery_api_key')
          setApiKey(null)
        }
        setAuthUser(null)
      } finally {
        setChecking(false)
      }
    }

    checkAuth()
  }, [setAuthUser, setSessionToken, setApiKey, setClassificationScope, setAuthRole])

  if (checking) {
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
