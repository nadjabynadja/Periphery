// ============================================
// PERIPHERY — User Menu (top bar dropdown)
// ============================================

import { useState } from 'react'
import { peripheryApi } from '../../api'
import { useStore } from '../../store'

export function UserMenu() {
  const authUser = useStore(s => s.authUser)
  const setAuthUser = useStore(s => s.setAuthUser)
  const setSessionToken = useStore(s => s.setSessionToken)
  const [open, setOpen] = useState(false)

  if (!authUser) return null

  const handleLogout = async () => {
    try {
      await peripheryApi.logout()
    } catch {
      // Ignore logout errors
    }
    setSessionToken(null)
    setAuthUser(null)
    setOpen(false)
  }

  const initials = authUser.display_name
    .split(' ')
    .map(w => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2 py-1 text-xxs"
        style={{ color: 'var(--text-secondary)' }}
      >
        <span
          className="w-5 h-5 flex items-center justify-center rounded-full text-[10px] font-bold"
          style={{ background: 'var(--accent-cyan)', color: 'var(--bg-primary)' }}
        >
          {initials}
        </span>
        <span className="font-display tracking-wider">{authUser.display_name}</span>
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-48 py-1 z-50"
          style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
        >
          <div className="px-3 py-2 border-b" style={{ borderColor: 'var(--border-color)' }}>
            <div className="text-xxs font-display tracking-wider" style={{ color: 'var(--text-dim)' }}>
              Organization
            </div>
            <div className="text-xs" style={{ color: 'var(--text-primary)' }}>
              {authUser.org_name || authUser.org_id.slice(0, 8)}
            </div>
            <div className="text-xxs mt-1" style={{ color: 'var(--text-dim)' }}>
              Role: {authUser.role}
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="w-full text-left px-3 py-2 text-xs hover:bg-base-700"
            style={{ color: 'var(--text-secondary)' }}
          >
            Sign Out
          </button>
        </div>
      )}
    </div>
  )
}
