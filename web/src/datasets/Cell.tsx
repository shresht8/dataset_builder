// Per-type display and edit widgets for the grid (design §4, §3 column
// types). Editors only ever produce values drawn from the column's own
// schema (options / number / boolean), so an invalid value is impossible by
// construction — there is no free-text path for select/multi_select.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { Column } from '../api/types'
import { isDate } from './dates'
import { formatJson, summarizeJson } from './json'

export function CellDisplay({ column, value }: { column: Column; value: unknown }) {
  if (column.type === 'json') {
    // One-line summary; the full value is in the tooltip and the drawer.
    return (
      <span className="cell-json" title={value === null || value === undefined ? undefined : formatJson(value)}>
        {summarizeJson(value)}
      </span>
    )
  }
  if (column.type === 'multi_select') {
    return <span>{Array.isArray(value) ? value.join(', ') : ''}</span>
  }
  if (column.type === 'number') {
    return <span>{typeof value === 'number' ? value : ''}</span>
  }
  return <span className={column.type === 'long_text' ? 'cell-preview' : undefined}>
    {typeof value === 'string' ? value : ''}
  </span>
}

interface EditorProps {
  column: Column
  draft: unknown
  onDraftChange: (value: unknown) => void
}

/** Text, number, and select editors — all commit/cancel via the grid's
 * shared Enter/Escape/Tab handling in DatasetPage. */
export function CellEditor({ column, draft, onDraftChange }: EditorProps) {
  if (column.type === 'date') {
    return <DateInput autoFocus value={typeof draft === 'string' ? draft : ''} onChange={onDraftChange} />
  }
  if (column.type === 'number') {
    return (
      <input
        autoFocus
        type="number"
        value={draft === null || draft === undefined ? '' : String(draft)}
        onChange={(event) =>
          onDraftChange(event.target.value === '' ? null : Number(event.target.value))
        }
      />
    )
  }
  if (column.type === 'select') {
    const value = typeof draft === 'string' ? draft : ''
    return (
      <select autoFocus value={value} onChange={(event) => onDraftChange(event.target.value)}>
        {(!column.required || value === '') && <option value="">—</option>}
        {(column.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  return (
    <input
      autoFocus
      type="text"
      value={typeof draft === 'string' ? draft : ''}
      onChange={(event) => onDraftChange(event.target.value)}
    />
  )
}

/** A `date` value: typed as YYYY-MM-DD, or picked with the native date
 * picker (whose value is already YYYY-MM-DD). Both edit the same string. */
export function DateInput({
  value,
  autoFocus = false,
  disabled = false,
  onChange,
}: {
  value: string
  autoFocus?: boolean
  disabled?: boolean
  onChange: (value: string) => void
}) {
  return (
    <span className="date-input">
      <input
        autoFocus={autoFocus}
        type="text"
        inputMode="numeric"
        placeholder="YYYY-MM-DD"
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <input
        type="date"
        aria-label="Pick a date"
        tabIndex={-1}
        disabled={disabled}
        value={isDate(value) ? value : ''}
        onChange={(event) => onChange(event.target.value)}
      />
    </span>
  )
}

interface MultiSelectEditorProps {
  column: Column
  draft: string[]
  onDraftChange: (value: string[]) => void
  onCommit: () => void
  onCancel: () => void
  onTab: (shiftKey: boolean) => void
}

/** multi_select is a tag picker over the column's fixed options — free text
 * is impossible. It owns arrow-key/space navigation among options, so it
 * intercepts keydown itself instead of going through the grid's handler. */
export function MultiSelectEditor({
  column,
  draft,
  onDraftChange,
  onCommit,
  onCancel,
  onTab,
}: MultiSelectEditorProps) {
  const options = column.options ?? []
  const [highlight, setHighlight] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)

  // React's `autoFocus` is a no-op on a plain div (it only wires up form
  // elements), so without this the picker never actually receives DOM focus
  // and its onKeyDown below never fires — focus silently stays on the <td>.
  useEffect(() => {
    rootRef.current?.focus()
  }, [])

  function toggle(option: string) {
    if (draft.includes(option)) onDraftChange(draft.filter((v) => v !== option))
    else onDraftChange([...draft, option])
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      event.preventDefault()
      event.stopPropagation()
      setHighlight((h) => Math.min(h + 1, options.length - 1))
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      event.preventDefault()
      event.stopPropagation()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (event.key === ' ') {
      event.preventDefault()
      event.stopPropagation()
      const option = options[highlight]
      if (option !== undefined) toggle(option)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      event.stopPropagation()
      onCommit()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onCancel()
    } else if (event.key === 'Tab') {
      event.preventDefault()
      event.stopPropagation()
      onTab(event.shiftKey)
    }
  }

  return (
    <div ref={rootRef} className="multi-select-editor" tabIndex={-1} onKeyDown={handleKeyDown}>
      {options.map((option, index) => (
        <label
          key={option}
          className={index === highlight ? 'ms-option ms-option-highlight' : 'ms-option'}
        >
          <input type="checkbox" tabIndex={-1} readOnly checked={draft.includes(option)} />
          {option}
        </label>
      ))}
    </div>
  )
}
