// Versions view + cut dialog (design §4, §6, GL-3-4). Lists past versions
// (viewer+) and, for editors/admins, cuts a new one from the current draft.
// Versions are immutable: there is no edit or delete action anywhere here.
// Escape closes it, same pattern as ImportWizard/CommentsPanel.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ApiError, cutVersion, listVersions } from '../api/client'
import type { VersionRead } from '../api/types'

interface VersionsPanelProps {
  datasetId: string
  canCut: boolean
  onClose: () => void
}

function shortHash(hash: string): string {
  const hex = hash.startsWith('sha256:') ? hash.slice('sha256:'.length) : hash
  return hex.slice(0, 12)
}

export function VersionsPanel({ datasetId, canCut, onClose }: VersionsPanelProps) {
  const [versions, setVersions] = useState<VersionRead[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [approvedOnly, setApprovedOnly] = useState(true)
  const [busy, setBusy] = useState(false)
  const [cutError, setCutError] = useState<string | null>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  function reload() {
    listVersions(datasetId)
      .then(setVersions)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load versions'))
  }

  useEffect(reload, [datasetId])

  useEffect(() => {
    // Without the cut form there's no field to focus; focus the dialog itself
    // so Escape reaches handleKeyDown and keys don't drive the grid behind it.
    if (canCut) noteRef.current?.focus()
    else dialogRef.current?.focus()
  }, [canCut])

  async function handleCut() {
    setBusy(true)
    setCutError(null)
    try {
      const created = await cutVersion(datasetId, {
        notes: notes.trim() === '' ? null : notes.trim(),
        include_unapproved: !approvedOnly,
      })
      setVersions((prev) => [created, ...(prev ?? [])])
      setNotes('')
      noteRef.current?.focus()
    } catch (err) {
      setCutError(err instanceof ApiError ? err.message : 'Could not cut version')
    } finally {
      setBusy(false)
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
      <div ref={dialogRef} tabIndex={-1} className="versions-panel" role="dialog" aria-modal="true" aria-label="Versions">
        <div className="drawer-header">
          <span>Versions</span>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="hint">
          A cut version is a permanent, immutable snapshot: it can never be edited or deleted. Correcting one means
          cutting another.
        </p>

        {canCut && (
          <div className="cut-version-form">
            <h3>Cut a new version</h3>
            <label>
              Notes
              <textarea
                ref={noteRef}
                value={notes}
                disabled={busy}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="What changed in this cut?"
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={approvedOnly}
                disabled={busy}
                onChange={(event) => setApprovedOnly(event.target.checked)}
              />
              Approved rows only
            </label>
            {cutError && <p className="error">{cutError}</p>}
            <button type="button" disabled={busy} onClick={() => void handleCut()}>
              {busy ? 'Cutting…' : 'Cut version'}
            </button>
          </div>
        )}

        {loadError && <p className="error">{loadError}</p>}
        {versions === null && !loadError && <p>Loading versions…</p>}
        {versions !== null && versions.length === 0 && <p>No versions cut yet.</p>}
        {versions !== null && versions.length > 0 && (
          <table className="import-table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Rows</th>
                <th>Hash</th>
                <th>Notes</th>
                <th>Author</th>
                <th>Cut at</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id}>
                  <td>v{v.version}</td>
                  <td>{v.row_count}</td>
                  <td>
                    <span title={v.content_hash} className="version-hash">
                      {shortHash(v.content_hash)}
                    </span>
                  </td>
                  <td>{v.notes ?? '—'}</td>
                  <td>{v.created_by_email ?? '—'}</td>
                  <td>{new Date(v.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
