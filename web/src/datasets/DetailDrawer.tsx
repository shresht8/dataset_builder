// Side panel for long_text and json fields (design §4, GL-3.5-14): values that
// don't fit in a grid cell are read and edited here. long_text saves on blur;
// json is edited as pretty-printed text with Format/Save, parsed client-side
// (save disabled while invalid) and checked against the column's JSON Schema
// by the API, whose error is shown inline. Escape closes the drawer, saving
// pending edits first; the grid refocuses the originating cell on unmount.
// Read-only (viewers): values are shown, nothing is editable.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { Column } from '../api/types'
import { formatJson, parseJsonText } from './json'

interface DetailDrawerProps {
  columns: Column[]
  data: Record<string, unknown>
  focusColKey?: string
  readOnly: boolean
  // Resolves to an error message to show beside the field, or null on success.
  onFieldSave: (colKey: string, value: unknown) => Promise<string | null>
  onClose: () => void
}

function textOf(column: Column, data: Record<string, unknown>): string {
  const value = data[column.key]
  if (column.type === 'json') return value === null || value === undefined ? '' : formatJson(value)
  return typeof value === 'string' ? value : ''
}

// Compare JSON values ignoring object key order (the API returns jsonb order).
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, (v as Record<string, unknown>)[k]]))
      : v,
  )
}

export function DetailDrawer({ columns, data, focusColKey, readOnly, onFieldSave, onClose }: DetailDrawerProps) {
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(columns.map((column) => [column.key, textOf(column, data)])),
  )
  const [errors, setErrors] = useState<Record<string, string | null>>({})

  // When a field's stored value changes underneath the drawer -- a save came
  // back, or "Take theirs" resolved a conflict -- show the new value. Keeping
  // the stale text would re-save it on close and undo the resolution.
  const shownData = useRef(data)
  useEffect(() => {
    const previous = shownData.current
    shownData.current = data
    const changed = columns.filter(
      (column) => canonical(previous[column.key] ?? null) !== canonical(data[column.key] ?? null),
    )
    if (changed.length === 0) return
    setTexts((prev) => ({ ...prev, ...Object.fromEntries(changed.map((c) => [c.key, textOf(c, data)])) }))
    setErrors((prev) => ({ ...prev, ...Object.fromEntries(changed.map((c) => [c.key, null])) }))
  }, [columns, data])

  function isChanged(column: Column): boolean {
    const text = texts[column.key] ?? ''
    if (column.type !== 'json') return text !== textOf(column, data)
    const parsed = parseJsonText(text)
    return !parsed.ok || canonical(parsed.value) !== canonical(data[column.key] ?? null)
  }

  async function save(column: Column) {
    if (readOnly || !isChanged(column)) return
    const text = texts[column.key] ?? ''
    let value: unknown = text
    if (column.type === 'json') {
      const parsed = parseJsonText(text)
      if (!parsed.ok) return
      value = parsed.value
    }
    const error = await onFieldSave(column.key, value)
    setErrors((prev) => ({ ...prev, [column.key]: error }))
  }

  function format(column: Column) {
    const parsed = parseJsonText(texts[column.key] ?? '')
    if (parsed.ok) setTexts((prev) => ({ ...prev, [column.key]: formatJson(parsed.value ?? undefined) }))
  }

  function close() {
    if (!readOnly) {
      const invalid = columns.find(
        (column) => column.type === 'json' && !parseJsonText(texts[column.key] ?? '').ok,
      )
      if (invalid) {
        setErrors((prev) => ({ ...prev, [invalid.key]: 'Fix or revert this JSON before closing.' }))
        return
      }
      columns.forEach((column) => void save(column))
    }
    onClose()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Row detail">
        <div className="drawer-header">
          <span>Row detail{readOnly ? ' (read-only)' : ''}</span>
          <button type="button" onClick={close}>
            Close
          </button>
        </div>
        {columns.map((column) => {
          const text = texts[column.key] ?? ''
          const parsed = column.type === 'json' ? parseJsonText(text) : null
          const fieldError = (parsed && !parsed.ok ? parsed.error : null) ?? errors[column.key] ?? null
          const label = `${column.label}${column.required ? ' *' : ''}`
          if (readOnly && column.type === 'json') {
            return (
              <details key={column.key} className="drawer-field" open>
                <summary>{label}</summary>
                <pre className="json-view">{text || '—'}</pre>
              </details>
            )
          }
          return (
            <div key={column.key} className="drawer-field">
              <label htmlFor={`drawer-${column.key}`}>{label}</label>
              <textarea
                id={`drawer-${column.key}`}
                className={column.type === 'json' ? 'json-editor' : undefined}
                autoFocus={column.key === focusColKey}
                readOnly={readOnly}
                spellCheck={column.type !== 'json'}
                value={text}
                aria-invalid={fieldError ? true : undefined}
                onChange={(event) => {
                  const value = event.target.value
                  setTexts((prev) => ({ ...prev, [column.key]: value }))
                  setErrors((prev) => ({ ...prev, [column.key]: null }))
                }}
                onBlur={() => {
                  if (column.type !== 'json') void save(column)
                }}
                onKeyDown={(event) => {
                  if (column.type === 'json' && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault()
                    void save(column)
                  }
                }}
              />
              {fieldError && (
                <p className="field-error" role="alert">
                  {fieldError}
                </p>
              )}
              {column.type === 'json' && !readOnly && (
                <div className="json-actions">
                  <button type="button" disabled={!parsed?.ok} onClick={() => format(column)}>
                    Format
                  </button>
                  <button
                    type="button"
                    disabled={!parsed?.ok || !isChanged(column)}
                    onClick={() => void save(column)}
                  >
                    Save
                  </button>
                  <span className="hint-inline">Ctrl+Enter saves</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
