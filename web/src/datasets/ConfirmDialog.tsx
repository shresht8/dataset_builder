// A small confirm dialog in the app's own style (never window.confirm, which
// blocks the page). Escape cancels.
import { useState, type KeyboardEvent, type ReactNode } from 'react'

interface ConfirmDialogProps {
  title: string
  children: ReactNode
  confirmLabel: string
  // Resolves to an error message to show, or null when done.
  onConfirm: () => Promise<string | null>
  onClose: () => void
}

export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onClose }: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function confirm() {
    setBusy(true)
    const message = await onConfirm()
    setBusy(false)
    if (message === null) onClose()
    else setError(message)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="conflict-dialog" role="alertdialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
        {error && <p className="field-error">{error}</p>}
        <div className="conflict-actions">
          <button type="button" onClick={onClose} autoFocus>
            Cancel
          </button>
          <button type="button" className="danger" disabled={busy} onClick={() => void confirm()}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
