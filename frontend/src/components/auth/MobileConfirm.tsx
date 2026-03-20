// ============================================
// PERIPHERY — Mobile Confirmation Page
// Phone-side of QR auth flow: user enters their
// approved email address, sees a 6-digit passcode
// to type on the desktop.
// ============================================

import { useState } from 'react'
import { peripheryApi } from '../../api'

interface MobileConfirmProps {
  challengeId: string
}

export function MobileConfirm({ challengeId }: MobileConfirmProps) {
  const [email, setEmail] = useState('')
  const [challengeCode, setChallengeCode] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleScan = async () => {
    if (!email.trim()) return
    setLoading(true)
    setError('')
    try {
      const res = await peripheryApi.scanChallengeByEmail(challengeId, email.trim())
      setChallengeCode(res.challenge_code)
      setDisplayName(res.display_name)
    } catch (err: any) {
      const msg: string = err.message || 'Authentication failed'
      if (msg.includes('403') || msg.toLowerCase().includes('not approved')) {
        setError('This email address is not approved for access.')
      } else if (msg.includes('400') || msg.toLowerCase().includes('expired')) {
        setError('This QR code has expired. Please reload and try again.')
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  if (challengeCode) {
    return (
      <div
        className="h-screen flex items-center justify-center"
        style={{ background: 'var(--bg-primary)' }}
      >
        <div
          className="w-full max-w-sm p-8 text-center"
          style={{
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
          }}
        >
          <h2
            className="text-lg font-display font-bold tracking-wider mb-2"
            style={{ color: 'var(--accent-cyan)' }}
          >
            PERIPHERY
          </h2>
          {displayName && (
            <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
              Welcome, <span style={{ color: 'var(--accent-cyan)' }}>{displayName}</span>
            </p>
          )}
          <p className="text-sm mb-3" style={{ color: 'var(--text-secondary)' }}>
            Enter this code on the desktop
          </p>
          <div
            className="text-4xl font-mono tracking-[0.5em] py-6"
            style={{ color: 'var(--accent-cyan)' }}
          >
            {challengeCode}
          </div>
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            This code expires shortly
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="h-screen flex items-center justify-center"
      style={{ background: 'var(--bg-primary)' }}
    >
      <div
        className="w-full max-w-sm p-8"
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
        }}
      >
        <h2
          className="text-lg font-display font-bold tracking-wider text-center mb-1"
          style={{ color: 'var(--accent-cyan)' }}
        >
          PERIPHERY
        </h2>
        <p className="text-xs text-center mb-6" style={{ color: 'var(--text-dim)' }}>
          Authenticate with your email
        </p>

        <label
          className="block text-xs font-display tracking-wider mb-1"
          style={{ color: 'var(--text-dim)' }}
        >
          Email address
        </label>
        <input
          type="email"
          placeholder="you@example.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') handleScan()
          }}
          autoFocus
          autoCapitalize="none"
          autoCorrect="off"
          className="w-full px-3 py-2 text-sm border rounded mb-4"
          style={{
            background: 'var(--bg-primary)',
            borderColor: email ? 'var(--accent-cyan)' : 'var(--border-color)',
            color: 'var(--text-primary)',
          }}
        />

        {error && (
          <p className="text-xs mb-3" style={{ color: '#ff5555' }}>
            {error}
          </p>
        )}

        <button
          onClick={handleScan}
          disabled={!email.trim() || loading}
          className="w-full px-4 py-2 text-sm font-display tracking-wider uppercase"
          style={{
            background:
              email.trim() && !loading ? 'var(--accent-cyan)' : 'var(--bg-tertiary)',
            color:
              email.trim() && !loading ? 'var(--bg-primary)' : 'var(--text-dim)',
            border: 'none',
            cursor: email.trim() && !loading ? 'pointer' : 'not-allowed',
          }}
        >
          {loading ? 'Checking…' : 'Authenticate'}
        </button>
      </div>
    </div>
  )
}
