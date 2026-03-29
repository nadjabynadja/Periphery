// ============================================
// LoginPage — QR challenge flow + API Key auth tab
// ============================================

import React, { useState, useEffect, useRef, useCallback } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { peripheryApi } from '../../api/client'
import { useStore } from '../../store'
import type { DataClassification } from '../../api/types'

type AuthTab = 'qr' | 'apikey'

export const LoginPage: React.FC = () => {
  const [tab, setTab] = useState<AuthTab>('qr')

  return (
    <div className="h-screen flex items-center justify-center bg-base-900 grid-texture">
      <div className="scanline-overlay" />
      <div className="w-full max-w-md mx-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-display font-bold tracking-wider text-text-bright">
            PERIPHERY
          </h1>
          <p className="data-readout mt-1">INTELLIGENCE CONSOLE</p>
        </div>

        {/* Auth card */}
        <div className="panel p-6">
          {/* Tab bar */}
          <div className="tab-bar mb-6">
            <button
              className={`tab-item ${tab === 'qr' ? 'active' : ''}`}
              onClick={() => setTab('qr')}
            >
              QR Login
            </button>
            <button
              className={`tab-item ${tab === 'apikey' ? 'active' : ''}`}
              onClick={() => setTab('apikey')}
            >
              API Key
            </button>
          </div>

          {tab === 'qr' ? <QRLoginFlow /> : <ApiKeyLoginFlow />}
        </div>

        <p className="text-center text-text-dim text-xxs mt-4 font-mono">
          SECURE ACCESS • TLS ENCRYPTED
        </p>
      </div>
    </div>
  )
}

// ---- QR Login Flow ----

const QRLoginFlow: React.FC = () => {
  const setAuthUser = useStore((s) => s.setAuthUser)
  const setSessionToken = useStore((s) => s.setSessionToken)
  const setClassificationScope = useStore((s) => s.setClassificationScope)
  const setAuthRole = useStore((s) => s.setAuthRole)

  const [challengeId, setChallengeId] = useState<string | null>(null)
  const [qrData, setQrData] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('idle')
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const startChallenge = useCallback(async () => {
    try {
      setError(null)
      setStatus('loading')
      const res = await peripheryApi.startChallenge()
      setChallengeId(res.challenge_id)
      setQrData(res.qr_data)
      setStatus('waiting')
    } catch (err: any) {
      setError(err?.message || 'Failed to start challenge')
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    startChallenge()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [startChallenge])

  useEffect(() => {
    if (!challengeId || status !== 'waiting') return

    pollRef.current = setInterval(async () => {
      try {
        const res = await peripheryApi.pollChallengeStatus(challengeId)
        if (res.status === 'confirmed' || res.status === 'scanned') {
          // Try to get session
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
            if (pollRef.current) clearInterval(pollRef.current)
            setStatus('complete')
          } catch {
            // Not ready yet
          }
        }
      } catch {
        // Polling error — continue
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [challengeId, status, setAuthUser, setSessionToken, setClassificationScope, setAuthRole])

  if (status === 'loading') {
    return (
      <div className="text-center py-8">
        <div className="calibrating w-24 mx-auto mb-4" />
        <span className="data-readout">GENERATING CHALLENGE…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-accent-red text-sm mb-4">{error}</p>
        <button className="btn-primary" onClick={startChallenge}>
          RETRY
        </button>
      </div>
    )
  }

  return (
    <div className="text-center">
      <p className="text-text-secondary text-xs mb-4">
        Scan with the Periphery mobile app to authenticate
      </p>
      {qrData && (
        <div className="inline-block p-4 bg-white rounded-sm mb-4">
          <QRCodeSVG value={qrData} size={200} level="M" />
        </div>
      )}
      <p className="data-readout">
        {status === 'waiting' ? 'AWAITING SCAN…' : 'AUTHENTICATED ✓'}
      </p>
    </div>
  )
}

// ---- API Key Login Flow ----

const ApiKeyLoginFlow: React.FC = () => {
  const setAuthUser = useStore((s) => s.setAuthUser)
  const setApiKey = useStore((s) => s.setApiKey)
  const setClassificationScope = useStore((s) => s.setClassificationScope)
  const setAuthRole = useStore((s) => s.setAuthRole)

  const [key, setKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<{ role: string; scope: string[] } | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!key.trim()) return

    setLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const me = await peripheryApi.loginWithApiKey(key.trim())
      setApiKey(key.trim())
      setAuthUser({
        user_id: me.user_id,
        org_id: me.org_id,
        org_name: me.org_name,
        display_name: me.display_name,
        role: me.role,
      })
      setAuthRole(me.role)
      const scope = me.classification_scope || ['PUBLIC']
      setClassificationScope(scope as DataClassification[])
      setSuccess({ role: me.role, scope })
    } catch (err: any) {
      setError(err?.message || 'Invalid API key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <p className="text-text-secondary text-xs mb-4">
        Paste your API key to authenticate
      </p>

      <input
        type="password"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder="pk_..."
        className="command-input mb-3"
        autoFocus
        disabled={loading}
      />

      {error && (
        <p className="text-accent-red text-xs mb-3">{error}</p>
      )}

      {success && (
        <div className="mb-3 p-2 bg-green-900/20 border border-green-700/30 rounded-sm">
          <p className="text-green-400 text-xs font-mono">
            ✓ Authenticated as <strong>{success.role}</strong>
          </p>
          <p className="text-green-400/70 text-xxs font-mono mt-1">
            Scope: {success.scope.join(', ')}
          </p>
        </div>
      )}

      <button
        type="submit"
        className="btn-primary w-full"
        disabled={loading || !key.trim()}
      >
        {loading ? 'VALIDATING…' : 'AUTHENTICATE'}
      </button>
    </form>
  )
}

export default LoginPage
