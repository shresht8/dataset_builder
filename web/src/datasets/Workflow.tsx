// Per-row status and assignee controls (design §4, GL-2-7). Plain native
// controls — fully keyboard-operable once focused, not wired into the
// grid's custom arrow-key cell navigation (that stays scoped to schema
// data columns, GL-2-6). Read-only (viewers, GL-3.5-14) shows plain text.
import type { Row, User } from '../api/types'

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'draft', label: 'Draft' },
  { value: 'needs_review', label: 'Needs review' },
  { value: 'approved', label: 'Approved' },
]

export function StatusControl({
  row,
  readOnly = false,
  onChange,
}: {
  row: Row
  readOnly?: boolean
  onChange: (status: string) => void
}) {
  if (readOnly) {
    return <span>{STATUS_OPTIONS.find((opt) => opt.value === row.status)?.label ?? row.status}</span>
  }
  return (
    <select aria-label="Row status" value={row.status} onChange={(event) => onChange(event.target.value)}>
      {STATUS_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}

interface AssigneeControlProps {
  row: Row
  currentUserId: string
  // Non-null only when the caller can list users (admin, §5); otherwise the
  // control falls back to assign-to-me/unassign only (GL-2-7 work log).
  users: User[] | null
  readOnly?: boolean
  onChange: (assignee: string | null) => void
}

export function AssigneeControl({ row, currentUserId, users, readOnly = false, onChange }: AssigneeControlProps) {
  const isMine = row.assignee === currentUserId
  const label = row.assignee === null ? 'Unassigned' : isMine ? 'You' : `User ${row.assignee.slice(0, 8)}`
  if (readOnly) return <span>{label}</span>

  if (users) {
    return (
      <select
        aria-label="Assignee"
        value={row.assignee ?? ''}
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
      >
        <option value="">Unassigned</option>
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.display_name}
          </option>
        ))}
      </select>
    )
  }

  return (
    <span className="assignee-control">
      <span>{label}</span>
      {isMine ? (
        <button type="button" onClick={() => onChange(null)}>
          Unassign
        </button>
      ) : (
        <button type="button" onClick={() => onChange(currentUserId)}>
          Assign to me
        </button>
      )}
    </span>
  )
}
