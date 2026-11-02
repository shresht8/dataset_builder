// API tokens (GL-3.5-19): create, list and revoke personal access tokens for
// the CLI and scripts (design §5 "Machine access"). A token acts as the user
// who created it -- a viewer's token can read and pull but not push. The raw
// token is shown once, right after creation, and is never stored here.
import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, createToken, listTokens, revokeToken } from '../api/client'
import type { ApiToken, ApiTokenCreated } from '../api/types'
import { ConfirmDialog } from '../datasets/ConfirmDialog'

const DEFAULT_DAYS = 90
const MAX_DAYS = 3650

function statusOf(token: ApiToken): 'active' | 'expired' | 'revoked' {
  if (token.revoked_at) return 'revoked'
  return new Date(token.expires_at).getTime() <= Date.now() ? 'expired' : 'active'
}

function NewTokenDialog({ created, onClose }: { created: ApiTokenCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const snippet = [
    `export GROUNDLINE_API_URL=${window.location.origin}`,
    `export GROUNDLINE_TOKEN=${created.token}`,
    'groundline datasets list',
  ].join('\n')

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.token)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="conflict-dialog" role="dialog" aria-modal="true" aria-label="New API token">
        <h3>Token "{created.name}" created</h3>
        <p>Copy it now: it won't be shown again.</p>
        <div className="token-secret">
          <code>{created.token}</code>
          <button type="button" autoFocus onClick={() => void copy()}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p>Use it with the CLI:</p>
        <pre className="cli-snippet">{snippet}</pre>
        <p className="hint-inline">
          Or put <code>api_url</code> and <code>token</code> in <code>~/.config/groundline/config.toml</code>.
        </p>
        <div className="conflict-actions">
          <button type="button" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

export function TokensPage() {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [days, setDays] = useState(DEFAULT_DAYS)
  const [busy, setBusy] = useState(false)
  // Held only in component state; dropped when the dialog closes.
  const [created, setCreated] = useState<ApiTokenCreated | null>(null)
  const [toRevoke, setToRevoke] = useState<ApiToken | null>(null)

  function load() {
    listTokens()
      .then(setTokens)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load tokens'))
  }

  useEffect(load, [])

  async function create(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setCreated(await createToken(name.trim(), days))
      setName('')
      setDays(DEFAULT_DAYS)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the token')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(token: ApiToken): Promise<string | null> {
    try {
      await revokeToken(token.id)
      load()
      return null
    } catch (err) {
      return err instanceof ApiError ? err.message : 'Could not revoke the token'
    }
  }

  return (
    <div className="tokens-page">
      <h2>API tokens</h2>
      <p>
        Tokens let the <code>groundline</code> CLI and scripts act as you, with your role. Revoke a token you no
        longer use.
      </p>
      <form onSubmit={(event) => void create(event)}>
        <label>
          Name
          <input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Expires in (days)
          <input
            type="number"
            min={1}
            max={MAX_DAYS}
            required
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          />
        </label>
        <button type="submit" disabled={busy || name.trim() === ''}>
          Create token
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {tokens === null ? (
        <p>Loading tokens…</p>
      ) : tokens.length === 0 ? (
        <p className="hint">No tokens yet.</p>
      ) : (
        <table className="import-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Expires</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => {
              const status = statusOf(token)
              return (
                <tr key={token.id} className={`token-status-${status}`}>
                  <td>{token.name}</td>
                  <td>{token.expires_at.slice(0, 10)}</td>
                  <td>{status}</td>
                  <td>
                    {status === 'active' && (
                      <button type="button" className="danger-link" onClick={() => setToRevoke(token)}>
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {created && <NewTokenDialog created={created} onClose={() => setCreated(null)} />}
      {toRevoke && (
        <ConfirmDialog
          title={`Revoke token "${toRevoke.name}"?`}
          confirmLabel="Revoke"
          onConfirm={() => revoke(toRevoke)}
          onClose={() => setToRevoke(null)}
        >
          <p>Anything still using it -- scripts, CI, the CLI -- will get "authentication failed".</p>
        </ConfirmDialog>
      )}
    </div>
  )
}
