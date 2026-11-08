// Reusable schema editor (Phase 4, GL-120): a controlled list of columns.
// Add, remove and reorder columns; per column edit key (dotted paths allowed),
// label, type, required, options (select / multi_select, as chips), the key
// column (at most one; forces text + required) and an optional JSON Schema for
// json columns. Client-side checks mirror the server (services/columns.py);
// a server 422 naming a column is shown on that column.
import { useState, type KeyboardEvent } from 'react'
import type { Column, ColumnInput, ColumnType } from '../api/types'

export const COLUMN_TYPES: ColumnType[] = [
  'text',
  'long_text',
  'select',
  'multi_select',
  'number',
  'boolean',
  'date',
  'json',
]

const OPTION_TYPES: ColumnType[] = ['select', 'multi_select']

// A column being edited: the wire fields plus a stable React key and the raw
// JSON Schema text (which may not parse yet).
export interface EditableColumn {
  uid: string
  key: string
  label: string
  type: ColumnType
  options: string[]
  required: boolean
  is_key: boolean
  jsonSchemaText: string
}

let nextUid = 0
const uid = () => `col-${(nextUid += 1)}`

export function toEditable(columns: (Column | ColumnInput)[]): EditableColumn[] {
  return columns.map((c) => ({
    uid: uid(),
    key: c.key,
    label: c.label,
    type: c.type,
    options: c.options ?? [],
    required: c.required,
    is_key: Boolean(c.is_key),
    jsonSchemaText: c.json_schema ? JSON.stringify(c.json_schema, null, 2) : '',
  }))
}

export function toInput(columns: EditableColumn[]): ColumnInput[] {
  return columns.map((c) => {
    const out: ColumnInput = { key: c.key.trim(), label: c.label.trim() || c.key.trim(), type: c.type, required: c.required }
    if (OPTION_TYPES.includes(c.type)) out.options = c.options
    if (c.is_key) out.is_key = true
    if (c.type === 'json' && c.jsonSchemaText.trim() !== '') {
      out.json_schema = JSON.parse(c.jsonSchemaText) as Record<string, unknown>
    }
    return out
  })
}

/** Problems keyed by column uid ('' = the whole schema). Empty when valid. */
export function columnProblems(columns: EditableColumn[]): Record<string, string> {
  const problems: Record<string, string> = {}
  const keys = new Set(columns.map((c) => c.key.trim()))
  const seen = new Set<string>()
  for (const col of columns) {
    const key = col.key.trim()
    if (key === '') problems[col.uid] = 'key is required'
    else if (key.split('.').some((part) => part === '')) problems[col.uid] = "every '.'-separated part must be non-empty"
    else if (seen.has(key)) problems[col.uid] = `duplicate column key '${key}'`
    else {
      const parts = key.split('.')
      for (let end = 1; end < parts.length; end += 1) {
        const prefix = parts.slice(0, end).join('.')
        if (keys.has(prefix)) problems[col.uid] = `'${prefix}' can't be both a value and an object`
      }
    }
    seen.add(key)
    if (!problems[col.uid] && OPTION_TYPES.includes(col.type) && col.options.length === 0) {
      problems[col.uid] = `${col.type} needs at least one option`
    }
    if (!problems[col.uid] && col.type === 'json' && col.jsonSchemaText.trim() !== '') {
      try {
        const parsed: unknown = JSON.parse(col.jsonSchemaText)
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          problems[col.uid] = 'JSON Schema must be an object'
        }
      } catch {
        problems[col.uid] = 'JSON Schema is not valid JSON'
      }
    }
  }
  if (columns.filter((c) => c.is_key).length > 1) problems[''] = 'only one key column is allowed'
  return problems
}

/** Which column a server message names, e.g. "column 'x': ..." or "duplicate column key: 'x'". */
export function columnForMessage(columns: EditableColumn[], message: string): string {
  // Look for each key in quotes rather than parsing quotes out of the message:
  // messages contain apostrophes too ("can't make 'x' the key column").
  let best: { uid: string; at: number } | null = null
  for (const col of columns) {
    const key = col.key.trim()
    const at = key ? message.indexOf(`'${key}'`) : -1
    if (at !== -1 && (best === null || at < best.at)) best = { uid: col.uid, at }
  }
  return best?.uid ?? ''
}

function OptionChips({
  options,
  disabled,
  onChange,
}: {
  options: string[]
  disabled: boolean
  onChange: (options: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  function add() {
    const value = draft.trim()
    if (value !== '' && !options.includes(value)) onChange([...options, value])
    setDraft('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      add()
    }
  }

  return (
    <span className="option-chips">
      {options.map((option) => (
        <span key={option} className="chip">
          {option}
          {!disabled && (
            <button type="button" aria-label={`Remove option ${option}`} onClick={() => onChange(options.filter((o) => o !== option))}>
              ×
            </button>
          )}
        </span>
      ))}
      {!disabled && (
        <input
          placeholder="add option, Enter"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={add}
        />
      )}
    </span>
  )
}

interface SchemaEditorProps {
  columns: EditableColumn[]
  // Omit for a read-only view.
  onChange?: (columns: EditableColumn[]) => void
  // A server 422 message; shown on the column it names, else above the list.
  serverError?: string | null
}

export function SchemaEditor({ columns, onChange, serverError }: SchemaEditorProps) {
  const readOnly = onChange === undefined
  const problems = columnProblems(columns)
  const serverUid = serverError ? columnForMessage(columns, serverError) : null

  function update(index: number, patch: Partial<EditableColumn>) {
    if (!onChange) return
    onChange(columns.map((col, i) => {
      if (i === index) {
        const next = { ...col, ...patch }
        if (next.is_key) {
          next.type = 'text'
          next.required = true
        }
        return next
      }
      // At most one key column.
      return patch.is_key ? { ...col, is_key: false } : col
    }))
  }

  function move(index: number, delta: number) {
    if (!onChange) return
    const target = index + delta
    if (target < 0 || target >= columns.length) return
    const next = [...columns]
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }

  return (
    <div className="schema-editor">
      {problems[''] && <p className="field-error">{problems['']}</p>}
      {serverError && serverUid === '' && <p className="field-error">{serverError}</p>}
      <table className="import-table schema-table">
        <thead>
          <tr>
            <th>Key</th>
            <th>Label</th>
            <th>Type</th>
            <th>Required</th>
            <th>Key column</th>
            <th>Options / JSON Schema</th>
            {!readOnly && <th />}
          </tr>
        </thead>
        <tbody>
          {columns.map((col, index) => {
            const problem = problems[col.uid] ?? (serverUid === col.uid ? serverError : null)
            return (
              <tr key={col.uid} className={problem ? 'row-invalid' : undefined}>
                <td>
                  <input
                    aria-label={`Column ${index + 1} key`}
                    value={col.key}
                    disabled={readOnly}
                    onChange={(event) => update(index, { key: event.target.value })}
                  />
                  {problem && <div className="field-error">{problem}</div>}
                </td>
                <td>
                  <input
                    aria-label={`Column ${index + 1} label`}
                    value={col.label}
                    disabled={readOnly}
                    onChange={(event) => update(index, { label: event.target.value })}
                  />
                </td>
                <td>
                  <select
                    aria-label={`Column ${index + 1} type`}
                    value={col.type}
                    disabled={readOnly || col.is_key}
                    onChange={(event) => update(index, { type: event.target.value as ColumnType })}
                  >
                    {COLUMN_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Column ${index + 1} required`}
                    checked={col.required}
                    disabled={readOnly || col.is_key}
                    onChange={(event) => update(index, { required: event.target.checked })}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Column ${index + 1} is the key column`}
                    checked={col.is_key}
                    disabled={readOnly}
                    onChange={(event) => update(index, { is_key: event.target.checked })}
                  />
                </td>
                <td>
                  {OPTION_TYPES.includes(col.type) && (
                    <OptionChips options={col.options} disabled={readOnly} onChange={(options) => update(index, { options })} />
                  )}
                  {col.type === 'json' && (
                    <textarea
                      className="json-editor"
                      aria-label={`Column ${index + 1} JSON Schema`}
                      placeholder="optional JSON Schema"
                      spellCheck={false}
                      value={col.jsonSchemaText}
                      disabled={readOnly}
                      onChange={(event) => update(index, { jsonSchemaText: event.target.value })}
                    />
                  )}
                </td>
                {!readOnly && (
                  <td className="schema-row-actions">
                    <button type="button" aria-label="Move up" disabled={index === 0} onClick={() => move(index, -1)}>
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label="Move down"
                      disabled={index === columns.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove column ${col.key || index + 1}`}
                      onClick={() => onChange?.(columns.filter((_, i) => i !== index))}
                    >
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
      {!readOnly && (
        <button
          type="button"
          onClick={() =>
            onChange?.([
              ...columns,
              { uid: uid(), key: '', label: '', type: 'text', options: [], required: false, is_key: false, jsonSchemaText: '' },
            ])
          }
        >
          Add column
        </button>
      )}
    </div>
  )
}
