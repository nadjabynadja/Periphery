// ============================================
// PERIPHERY — QR Code Login Page
// Desktop shows QR code, polls for scan,
// then shows passcode input for confirmation.
// ============================================

import { useCallback, useEffect, useRef, useState } from 'react'
import { peripheryApi } from '../../api'
import { useStore } from '../../store'

type LoginStage = 'qr' | 'passcode' | 'success' | 'error'

export function LoginPage() {
  const setAuthUser = useStore(s => s.setAuthUser)
  const setSessionToken = useStore(s => s.setSessionToken)

  const [stage, setStage] = useState<LoginStage>('qr')
  const [challengeId, setChallengeId] = useState('')
  const [qrData, setQrData] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [passcode, setPasscode] = useState('')
  const [userName, setUserName] = useState('')
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const startChallenge = useCallback(async () => {
    try {
      setError('')
      const res = await peripheryApi.startChallenge()
      setChallengeId(res.challenge_id)
      setQrData(res.qr_data)
      setExpiresAt(res.expires_at)
      setStage('qr')
    } catch (err: any) {
      setError(err.message || 'Failed to start login')
      setStage('error')
    }
  }, [])

  // Start challenge on mount
  useEffect(() => {
    startChallenge()
  }, [startChallenge])

  // Poll for challenge status while showing QR
  useEffect(() => {
    if (stage !== 'qr' || !challengeId) return

    pollRef.current = setInterval(async () => {
      try {
        const status = await peripheryApi.pollChallengeStatus(challengeId)
        if (status.status === 'scanned') {
          setUserName(status.user_display_name || '')
          setStage('passcode')
        } else if (status.status === 'expired') {
          setError('Challenge expired. Please try again.')
          setStage('error')
        }
      } catch {
        // Ignore poll errors
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [stage, challengeId])

  const handleConfirm = async () => {
    try {
      setError('')
      const res = await peripheryApi.confirmChallenge(challengeId, passcode)
      setSessionToken(res.session_token)
      setAuthUser({
        user_id: res.user_id,
        org_id: res.org_id,
        org_name: '',
        display_name: res.display_name,
        role: res.role,
      })
      setStage('success')
    } catch (err: any) {
      setError(err.message || 'Invalid passcode')
    }
  }

  return (
    <div className="h-screen w-screen flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
      <div className="scanline-overlay" />
      <div className="w-full max-w-md p-8" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
        <h1 className="text-xl font-display font-bold tracking-wider text-center mb-1" style={{ color: 'var(--accent-cyan)' }}>
          PERIPHERY
        </h1>
        <p className="text-xs text-center mb-6" style={{ color: 'var(--text-dim)' }}>
          Intelligence Console
        </p>

        {stage === 'qr' && (
          <div className="text-center">
            <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
              Scan this QR code with your device to authenticate
            </p>
            <div className="inline-block p-4 bg-white rounded mb-4">
              {/* Render QR data as a text-based placeholder until qrcode.react is installed */}
              <div className="w-48 h-48 flex items-center justify-center text-xs text-gray-500 font-mono break-all p-2 border border-gray-200">
                <QRDisplay data={qrData} />
              </div>
            </div>
            <p className="text-xxs" style={{ color: 'var(--text-dim)' }}>
              Waiting for scan...
            </p>
            {expiresAt && (
              <p className="text-xxs mt-1" style={{ color: 'var(--text-dim)' }}>
                Expires: {new Date(expiresAt).toLocaleTimeString()}
              </p>
            )}
          </div>
        )}

        {stage === 'passcode' && (
          <div className="text-center">
            <p className="text-sm mb-2" style={{ color: 'var(--text-secondary)' }}>
              Scanned by: <span style={{ color: 'var(--accent-cyan)' }}>{userName}</span>
            </p>
            <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
              Enter the 6-digit passcode shown on your device
            </p>
            <input
              type="text"
              maxLength={6}
              value={passcode}
              onChange={e => setPasscode(e.target.value.replace(/\D/g, ''))}
              onKeyDown={e => { if (e.key === 'Enter' && passcode.length === 6) handleConfirm() }}
              className="w-48 text-center text-2xl font-mono tracking-[0.5em] p-3 mb-4 border rounded"
              style={{
                background: 'var(--bg-primary)',
                borderColor: 'var(--border-color)',
                color: 'var(--text-primary)',
              }}
              autoFocus
              placeholder="------"
            />
            <br />
            <button
              onClick={handleConfirm}
              disabled={passcode.length !== 6}
              className="px-6 py-2 text-sm font-display tracking-wider uppercase"
              style={{
                background: passcode.length === 6 ? 'var(--accent-cyan)' : 'var(--bg-tertiary)',
                color: passcode.length === 6 ? 'var(--bg-primary)' : 'var(--text-dim)',
                border: 'none',
                cursor: passcode.length === 6 ? 'pointer' : 'not-allowed',
              }}
            >
              Confirm
            </button>
          </div>
        )}

        {stage === 'error' && (
          <div className="text-center">
            <p className="text-sm mb-4" style={{ color: '#ff5555' }}>{error}</p>
            <button
              onClick={startChallenge}
              className="px-6 py-2 text-sm font-display tracking-wider uppercase"
              style={{ background: 'var(--accent-cyan)', color: 'var(--bg-primary)', border: 'none', cursor: 'pointer' }}
            >
              Try Again
            </button>
          </div>
        )}

        {error && stage !== 'error' && (
          <p className="text-xs text-center mt-4" style={{ color: '#ff5555' }}>{error}</p>
        )}
      </div>
    </div>
  )
}


function QRDisplay({ data }: { data: string }) {
  // Simple text-based QR data display. Replace with qrcode.react when installed.
  if (!data) return <span>Loading...</span>

  try {
    const parsed = JSON.parse(data)
    return (
      <div className="text-left">
        <div className="text-[10px] leading-tight">
          <div>ID: {parsed.challenge_id?.slice(0, 12)}...</div>
          <div>URL: {parsed.server_url}</div>
        </div>
      </div>
    )
  } catch {
    return <span>{data.slice(0, 100)}</span>
  }
}
