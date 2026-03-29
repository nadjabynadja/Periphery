// ============================================
// UserMenu — dropdown for user info, role, logout
// ============================================

import React, { useState, useRef, useEffect } from 'react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'

export const UserMenu: React.FC = () => {
  const authUser = useStore((s) => s.authUser)
  const authRole = useStore((s) => s.authRole)
  const classificationScope = useStore((s) => s.classificationScope)
  const setAuthUser = useStore((s) => s.setAuthUser)
  const setSessionToken = useStore((s) => s.setSessionToken)
  const setApiKey = useStore((s) => s.setApiKey)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleLogout = async () => {
    try {
      await peripheryApi.logout()
    } catch {
      // Logout endpoint may fail if session already expired
    }
    localStorage.removeItem('periphery_session')
    localStorage.removeItem('periphery_api_key')
    setSessionToken(null)
    setApiKey(null)
    setAuthUser(null)
  }

  if (!authUser) return null

  const initials = authUser.display_name
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-2 py-1 rounded-sm hover:bg-base-500/50 transition-colors"
      >
        <div className="w-6 h-6 rounded-sm bg-accent-cyan/20 border border-accent-cyan/30 flex items-center justify-center">
          <span className="text-xxs font-mono text-accent-cyan font-bold">{initials}</span>
        </div>
        <span className="text-xxs font-mono text-text-secondary hidden sm:inline">
          {authUser.display_name}
        </span>
      </button>

      {open && (
        <div className="context-menu right-0 top-full mt-1 w-56">
          <div className="px-3 py-2 border-b border-surface-border">
            <p className="text-xs text-text-primary font-medium">{authUser.display_name}</p>
            <p className="text-xxs font-mono text-text-dim">{authUser.org_name}</p>
          </div>

          <div className="px-3 py-2 border-b border-surface-border">
            <div className="flex items-center justify-between">
              <span className="text-xxs text-text-dim">Role</span>
              <span className="text-xxs font-mono text-accent-cyan uppercase">{authRole}</span>
            </div>
            <div className="flex items-center justify-between mt-1">
              <span className="text-xxs text-text-dim">Scope</span>
              <span className="text-xxs font-mono text-text-secondary">
                {classificationScope.join(', ')}
              </span>
            </div>
          </div>

          <button
            className="context-menu-item text-accent-red"
            onClick={handleLogout}
          >
            ⏻ Logout
          </button>
        </div>
      )}
    </div>
  )
}

export default UserMenu
