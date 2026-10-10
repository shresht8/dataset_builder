// Delete a dataset (GL-3.5-18): only while it has no versions, confirmed by
// typing its name. Escape closes it, same pattern as the other dialogs.
import { useState, type KeyboardEvent } from 'react'
import { ApiError, deleteDataset } from '../api/client'

interface DeleteDatasetDialogProps {
  datasetId: string
  name: string
  onDeleted: () => void
  onClose: () => void
}

export function DeleteDatasetDialog({ datasetId, name, onDeleted, onClose }: DeleteDatasetDialogProps) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await deleteDataset(datasetId)
      onDeleted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Delete failed')
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
      <div className="conflict-dialog" role="alertdialog" aria-modal="true" aria-label="Delete dataset">
        <h3>Delete dataset {name}?</h3>
        <p>
          Its rows, columns, comments and history are deleted, and the name becomes free again. This can't be
          undone.
        </p>
        <label className="confirm-name">
          <span>
            Type <code>{name}</code> to confirm
          </span>
          <input autoFocus value={typed} onChange={(event) => setTyped(event.target.value)} />
        </label>
        {error && <p className="field-error">{error}</p>}
        <div className="conflict-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="danger" disabled={busy || typed !== name} onClick={() => void confirm()}>
            Delete dataset
          </button>
        </div>
      </div>
    </div>
  )
}
