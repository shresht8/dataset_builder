// Import wizard (design §4 Import, GL-2-9): upload -> map source columns
// onto the schema -> review validation errors with inline fix/skip -> commit.
// Escape closes it, same pattern as CommentsPanel/DetailDrawer.
//
// The backend's `POST /import` (GL-2-8) has no server-side import session:
// each call re-parses the whole file and re-applies `skip`/`fixes` from
// scratch. So a retry round (fix more rows, import again) would re-create
// rows already imported in an earlier round unless they're carried forward
// in `skip`. `handledRows` tracks exactly that: every row index already
// imported or explicitly skipped, folded into `skip` on every subsequent
// commit call.
import { useState, type KeyboardEvent } from 'react'
import { ApiError, commitImport, previewImport } from '../api/client'
import type { Column, ImportSampleRow, ImportValidationError } from '../api/types'

interface ImportWizardProps {
  datasetId: string
  columns: Column[]
  onClose: () => void
  onImported: () => void
}

type Step = 'upload' | 'mapping' | 'review'

function FixInput({
  column,
  value,
  disabled,
  onChange,
}: {
  column: Column | undefined
  value: unknown
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  if (column?.type === 'number') {
    return (
      <input
        type="number"
        disabled={disabled}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
      />
    )
  }
  if (column?.type === 'boolean') {
    return (
      <input
        type="checkbox"
        disabled={disabled}
        checked={Boolean(value)}
        onChange={(event) => onChange(event.target.checked)}
      />
    )
  }
  if (column?.type === 'select') {
    const current = typeof value === 'string' ? value : ''
    return (
      <select disabled={disabled} value={current} onChange={(event) => onChange(event.target.value)}>
        <option value="">—</option>
        {(column.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  if (column?.type === 'multi_select') {
    const selected = Array.isArray(value) ? (value as string[]) : []
    return (
      <span className="fix-multiselect">
        {(column.options ?? []).map((option) => (
          <label key={option} className="ms-option">
            <input
              type="checkbox"
              disabled={disabled}
              checked={selected.includes(option)}
              onChange={(event) =>
                onChange(event.target.checked ? [...selected, option] : selected.filter((v) => v !== option))
              }
            />
            {option}
          </label>
        ))}
      </span>
    )
  }
  return (
    <input
      type="text"
      disabled={disabled}
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

export function ImportWizard({ datasetId, columns, onClose, onImported }: ImportWizardProps) {
  const activeColumns = columns.filter((c) => !c.archived)
  const columnsByKey = new Map(activeColumns.map((c) => [c.key, c]))

  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [sourceColumns, setSourceColumns] = useState<string[]>([])
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [sampleRows, setSampleRows] = useState<ImportSampleRow[]>([])
  const [totalRows, setTotalRows] = useState(0)
  const [validCount, setValidCount] = useState(0)
  const [errors, setErrors] = useState<ImportValidationError[]>([])
  const [fixes, setFixes] = useState<Record<number, Record<string, unknown>>>({})
  const [activeSkip, setActiveSkip] = useState<Set<number>>(new Set())
  const [handledRows, setHandledRows] = useState<Set<number>>(new Set())
  const [totals, setTotals] = useState({ imported: 0, skipped: 0 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function handleFile(chosen: File) {
    setFile(chosen)
    setBusy(true)
    setError(null)
    try {
      const preview = await previewImport(datasetId, chosen)
      setSourceColumns(preview.columns)
      setMapping(preview.suggested_mapping)
      setSampleRows(preview.sample_rows)
      setTotalRows(preview.total_rows)
      setStep('mapping')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not read file')
    } finally {
      setBusy(false)
    }
  }

  function setColumnMapping(sourceCol: string, schemaKey: string) {
    setMapping((prev) => {
      const next = { ...prev }
      if (schemaKey === '') delete next[sourceCol]
      else next[sourceCol] = schemaKey
      return next
    })
  }

  async function confirmMapping() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const preview = await previewImport(datasetId, file, mapping)
      setValidCount(preview.validation?.valid ?? 0)
      setErrors(preview.validation?.errors ?? [])
      setStep('review')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not validate mapping')
    } finally {
      setBusy(false)
    }
  }

  function setFix(row: number, colKey: string, value: unknown) {
    setFixes((prev) => ({ ...prev, [row]: { ...prev[row], [colKey]: value } }))
  }

  function toggleSkip(row: number) {
    setActiveSkip((prev) => {
      const next = new Set(prev)
      if (next.has(row)) next.delete(row)
      else next.add(row)
      return next
    })
  }

  async function confirmImport() {
    if (!file) return
    setBusy(true)
    setError(null)
    const effectiveSkip = new Set([...handledRows, ...activeSkip])
    try {
      const result = await commitImport(datasetId, file, mapping, [...effectiveSkip], fixes)
      const remainingRows = new Set(result.errors.map((e) => e.row))
      const importedThisRound = new Set<number>()
      for (let row = 1; row <= totalRows; row += 1) {
        if (!effectiveSkip.has(row) && !remainingRows.has(row)) importedThisRound.add(row)
      }
      setHandledRows(new Set([...effectiveSkip, ...importedThisRound]))
      setActiveSkip(new Set())
      setFixes((prev) => {
        const next: Record<number, Record<string, unknown>> = {}
        for (const row of remainingRows) if (prev[row]) next[row] = prev[row]
        return next
      })
      setErrors(result.errors)
      setTotals((prev) => ({
        imported: prev.imported + result.imported,
        skipped: prev.skipped + result.skipped,
      }))
      if (result.errors.length === 0) {
        setDone(true)
        onImported()
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed')
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
      <div className="import-wizard" role="dialog" aria-modal="true" aria-label="Import rows">
        <div className="drawer-header">
          <span>Import rows</span>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        {error && <p className="error">{error}</p>}

        {step === 'upload' && (
          <div>
            <p>Upload a CSV or XLSX file to import rows.</p>
            <input
              type="file"
              accept=".csv,.xlsx"
              disabled={busy}
              onChange={(event) => {
                const chosen = event.target.files?.[0]
                if (chosen) void handleFile(chosen)
              }}
            />
          </div>
        )}

        {step === 'mapping' && (
          <div>
            <table className="import-table">
              <thead>
                <tr>
                  <th>Source column</th>
                  <th>Maps to</th>
                </tr>
              </thead>
              <tbody>
                {sourceColumns.map((sourceCol) => {
                  const usedElsewhere = new Set(
                    Object.entries(mapping)
                      .filter(([src]) => src !== sourceCol)
                      .map(([, key]) => key),
                  )
                  return (
                    <tr key={sourceCol}>
                      <td>{sourceCol}</td>
                      <td>
                        <select
                          value={mapping[sourceCol] ?? ''}
                          onChange={(event) => setColumnMapping(sourceCol, event.target.value)}
                        >
                          <option value="">Ignore</option>
                          {activeColumns
                            .filter((col) => !usedElsewhere.has(col.key))
                            .map((col) => (
                              <option key={col.key} value={col.key}>
                                {col.label}
                              </option>
                            ))}
                        </select>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p>
              {totalRows} row{totalRows === 1 ? '' : 's'} detected. Preview of the first {sampleRows.length}:
            </p>
            <div className="import-preview-scroll">
              <table className="import-table">
                <thead>
                  <tr>
                    {sourceColumns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sampleRows.map((r) => (
                    <tr key={r.row}>
                      {sourceColumns.map((c) => (
                        <td key={c}>{r.values[c]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button type="button" disabled={busy} onClick={() => void confirmMapping()}>
              Continue
            </button>
          </div>
        )}

        {step === 'review' && (
          <div>
            {done ? (
              <div>
                <p>
                  Import complete: {totals.imported} imported, {totals.skipped} skipped.
                </p>
                <button type="button" onClick={onClose}>
                  Done
                </button>
              </div>
            ) : (
              <>
                <p>
                  {validCount} row{validCount === 1 ? '' : 's'} valid.
                  {errors.length > 0 && ` ${errors.length} error${errors.length === 1 ? '' : 's'} to resolve.`}
                </p>
                {errors.length > 0 && (
                  <table className="import-table">
                    <thead>
                      <tr>
                        <th>Row</th>
                        <th>Column</th>
                        <th>Reason</th>
                        <th>Fix</th>
                        <th>Skip</th>
                      </tr>
                    </thead>
                    <tbody>
                      {errors.map((e) => {
                        const col = columnsByKey.get(e.column)
                        const skipped = activeSkip.has(e.row)
                        return (
                          <tr key={`${e.row}:${e.column}`}>
                            <td>{e.row}</td>
                            <td>{col?.label ?? e.column}</td>
                            <td>{e.reason}</td>
                            <td>
                              <FixInput
                                column={col}
                                value={fixes[e.row]?.[e.column]}
                                disabled={skipped}
                                onChange={(value) => setFix(e.row, e.column, value)}
                              />
                            </td>
                            <td>
                              <input
                                type="checkbox"
                                checked={skipped}
                                onChange={() => toggleSkip(e.row)}
                                aria-label={`Skip row ${e.row}`}
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
                <button type="button" disabled={busy} onClick={() => void confirmImport()}>
                  {errors.length > 0 ? 'Fix/skip and import' : 'Confirm import'}
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
