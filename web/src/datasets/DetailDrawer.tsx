// Side panel for long_text fields (design §4): inputs/expected outputs don't
// fit in a grid cell, so they're edited here in full-height textareas.
// Escape closes it (and flushes any unsaved field first); the grid refocuses
// the originating cell once this unmounts.
import { useState, type KeyboardEvent } from 'react'
import type { Column } from '../api/types'

interface DetailDrawerProps {
  columns: Column[]
  data: Record<string, unknown>
  focusColKey?: string
  onFieldSave: (colKey: string, value: string) => void
  onClose: () => void
}

function textOf(data: Record<string, unknown>, key: string): string {
  const value = data[key]
  return typeof value === 'string' ? value : ''
}

export function DetailDrawer({ columns, data, focusColKey, onFieldSave, onClose }: DetailDrawerProps) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    for (const column of columns) initial[column.key] = textOf(data, column.key)
    return initial
  })

  function flush(column: Column) {
    const value = values[column.key] ?? ''
    if (value !== textOf(data, column.key)) onFieldSave(column.key, value)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      columns.forEach(flush)
      onClose()
    }
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Row detail">
        <div className="drawer-header">
          <span>Row detail</span>
          <button
            type="button"
            onClick={() => {
              columns.forEach(flush)
              onClose()
            }}
          >
            Close
          </button>
        </div>
        {columns.map((column) => (
          <label key={column.key} className="drawer-field">
            <span>
              {column.label}
              {column.required ? ' *' : ''}
            </span>
            <textarea
              autoFocus={column.key === focusColKey}
              value={values[column.key] ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, [column.key]: event.target.value }))
              }
              onBlur={() => flush(column)}
            />
          </label>
        ))}
      </div>
    </div>
  )
}
