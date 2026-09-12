// 409 conflict UI (design §4 concurrency): shown whenever a row PATCH loses
// the optimistic-lock race. No silent data loss — the user must explicitly
// take the server's state or re-apply their own change on the fresh rev.
import type { KeyboardEvent } from 'react'
import type { RowPatchPayload } from '../api/client'
import type { Column, Row } from '../api/types'

interface ConflictDialogProps {
  columns: Column[]
  attempted: RowPatchPayload
  theirs: Row
  onTakeTheirs: () => void
  onRetryMine: () => void
}

function labelFor(columns: Column[], key: string): string {
  return columns.find((c) => c.key === key)?.label ?? key
}

function describe(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.join(', ') || '—'
  return String(value)
}

export function ConflictDialog({ columns, attempted, theirs, onTakeTheirs, onRetryMine }: ConflictDialogProps) {
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // No Escape-to-dismiss: the conflict must be resolved explicitly.
    if (event.key === 'Tab') event.stopPropagation()
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="conflict-dialog" role="alertdialog" aria-modal="true" aria-label="Edit conflict">
        <h3>This row changed since you loaded it</h3>
        <p>Someone else saved a change first. Review the difference before continuing.</p>
        <table className="conflict-table">
          <thead>
            <tr>
              <th></th>
              <th>Your change</th>
              <th>Current on server</th>
            </tr>
          </thead>
          <tbody>
            {attempted.data &&
              Object.entries(attempted.data).map(([key, value]) => (
                <tr key={key}>
                  <td>{labelFor(columns, key)}</td>
                  <td>{describe(value)}</td>
                  <td>{describe(theirs.data[key])}</td>
                </tr>
              ))}
            {attempted.status && (
              <tr>
                <td>Status</td>
                <td>{describe(attempted.status)}</td>
                <td>{describe(theirs.status)}</td>
              </tr>
            )}
            {'assignee' in attempted && (
              <tr>
                <td>Assignee</td>
                <td>{describe(attempted.assignee)}</td>
                <td>{describe(theirs.assignee)}</td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="conflict-actions">
          <button type="button" onClick={onTakeTheirs}>
            Take theirs (discard my change)
          </button>
          <button type="button" onClick={onRetryMine} autoFocus>
            Retry mine (re-apply on latest)
          </button>
        </div>
      </div>
    </div>
  )
}
