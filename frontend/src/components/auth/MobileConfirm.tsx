// ============================================
// PERIPHERY — Mobile Confirmation Page
// Phone-side of QR auth flow: user selects
// identity and sees the passcode to enter on desktop.
// ============================================

import { useEffect, useState } from 'react'
import { peripheryApi } from '../../api'

interface MobileConfirmProps {
  challengeId: string
}

export function MobileConfirm({ challengeId }: MobileConfirmProps) {
  const [users, setUsers] = useState<{ user_id: string; display_name: string; role: string }[]>([])
  const [orgs, setOrgs] = useState<{ org_id: string; name: string }[]>([])
  const [selectedUserId, setSelectedUserId] = useState('')
  const [challengeCode, setChallengeCode] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function loadOrgs() {
      try {
        const orgList = await peripheryApi.listOrgs()
        setOrgs(orgList)
      } catch (err: any) {
        setError(err.message || 'Failed to load organizations')
      } finally {
        setLoading(false)
      }
    }
    loadOrgs()
  }, [])

  const handleScan = async () => {
    if (!selectedUserId) return
    try {
      setError('')
      const res = await peripheryApi.scanChallenge(challengeId, selectedUserId)
      setChallengeCode(res.challenge_code)
    } catch (err: any) {
      setError(err.message || 'Failed to scan challenge')
    }
  }

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <p style={{ color: 'var(--text-dim)' }}>Loading...</p>
      </div>
    )
  }

  if (challengeCode) {
    return (
      <div className="h-screen flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <div className="w-full max-w-sm p-8 text-center" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <p className="text-sm mb-2" style={{ color: 'var(--text-secondary)' }}>
            Enter this code on the desktop
          </p>
          <div className="text-4xl font-mono tracking-[0.5em] py-4" style={{ color: 'var(--accent-cyan)' }}>
            {challengeCode}
          </div>
          <p className="text-xxs" style={{ color: 'var(--text-dim)' }}>
            This code will expire shortly
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
      <div className="w-full max-w-sm p-8" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
        <h2 className="text-lg font-display font-bold tracking-wider text-center mb-4" style={{ color: 'var(--accent-cyan)' }}>
          Confirm Identity
        </h2>

        <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
          Select your account to authenticate the desktop session
        </p>

        {orgs.map(org => (
          <div key={org.org_id} className="mb-2">
            <div className="text-xs font-display tracking-wider mb-1" style={{ color: 'var(--text-dim)' }}>
              {org.name}
            </div>
            <OrgUsers orgId={org.org_id} selectedUserId={selectedUserId} onSelect={setSelectedUserId} />
          </div>
        ))}

        {error && <p className="text-xs mt-2" style={{ color: '#ff5555' }}>{error}</p>}

        <button
          onClick={handleScan}
          disabled={!selectedUserId}
          className="w-full mt-4 px-4 py-2 text-sm font-display tracking-wider uppercase"
          style={{
            background: selectedUserId ? 'var(--accent-cyan)' : 'var(--bg-tertiary)',
            color: selectedUserId ? 'var(--bg-primary)' : 'var(--text-dim)',
            border: 'none',
            cursor: selectedUserId ? 'pointer' : 'not-allowed',
          }}
        >
          Authenticate
        </button>
      </div>
    </div>
  )
}


function OrgUsers({
  orgId,
  selectedUserId,
  onSelect,
}: {
  orgId: string
  selectedUserId: string
  onSelect: (id: string) => void
}) {
  const [users, setUsers] = useState<{ user_id: string; display_name: string; role: string }[]>([])

  useEffect(() => {
    // This endpoint requires auth, so for bootstrap the mobile confirm
    // page uses a special unprotected list. For now, we skip loading
    // and let the user enter their user_id manually if needed.
  }, [orgId])

  return (
    <div>
      <input
        type="text"
        placeholder="Enter your User ID"
        className="w-full px-3 py-2 text-sm border rounded"
        style={{
          background: 'var(--bg-primary)',
          borderColor: selectedUserId ? 'var(--accent-cyan)' : 'var(--border-color)',
          color: 'var(--text-primary)',
        }}
        onChange={e => onSelect(e.target.value)}
      />
    </div>
  )
}
