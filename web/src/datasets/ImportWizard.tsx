// Import wizard (design §4 Import, GL-2-9): upload -> map source columns
// onto the schema -> review validation errors with inline fix/skip -> commit.
// Escape closes it, same pattern as CommentsPanel/DetailDrawer.
//
// JSON / JSONL / YAML (GL-3.5-6): nested records arrive as dotted source
// columns; the API reports which list the records came from (and offers the
// candidates when it can't tell). Import stays create-only: a key that
// already exists is a row error pointing at `rows push`.
//
// The backend's `POST /import` (GL-2-8) has no server-side import session:
// each call re-parses the whole file and re-applies `skip`/`fixes` from
// scratch. So a retry round (fix more rows, import again) would re-create
// rows already imported in an earlier round unless they're carried forward
// in `skip`. `handledRows` tracks exactly that: every row index already
// imported or explicitly skipped, folded into `skip` on every subsequent
// commit call.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ApiError, commitImport, previewImport } from '../api/client'
import type {
  Column,
  ImportPreviewResponse,
  ImportSampleRow,
  ImportValidationError,
  ImportValidationReport,
} from '../api/types'
import { labelList, mappingCompat } from './compat'
import { DateInput } from './Cell'
import { formatJson, parseJsonText, summarizeJson } from './json'

const ACCEPTED = '.csv,.xlsx,.json,.jsonl,.ndjson,.yaml,.yml'

// Sample values are strings for CSV/XLSX and typed for JSON/YAML.
function showValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return summarizeJson(value)
  return String(value)
}

// Shown instead of the API's "choose a records_key" message (GL-132).
export const RECORDS_LIST_QUESTION = 'This file has more than one list. Which one holds your data?'

function candidatesOf(err: unknown): string[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null
  const body = err.body as { records_key_candidates?: unknown } | null
  return Array.isArray(body?.records_key_candidates) ? (body.records_key_candidates as string[]) : null
}

/** Inline fix for a json column: applied only while it parses. Unparseable text
 * clears the fix, so the row fails again on its original error instead of
 * quietly importing an earlier value. */
function JsonFixInput({
  value,
  disabled,
  onChange,
}: {
  value: unknown
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  const [text, setText] = useState(() => (value === undefined ? '' : formatJson(value)))
  const parsed = parseJsonText(text)
  return (
    <span className="json-fix">
      <textarea
        className="json-editor"
        disabled={disabled}
        spellCheck={false}
        value={text}
        aria-invalid={parsed.ok ? undefined : true}
        onChange={(event) => {
          setText(event.target.value)
          const next = parseJsonText(event.target.value)
          onChange(next.ok ? next.value : undefined)
        }}
      />
      {!parsed.ok && <span className="field-error">{parsed.error}</span>}
    </span>
  )
}

interface ImportWizardProps {
  datasetId: string
  columns: Column[]
  onClose: () => void
  onImported: () => void
  // Phase 4 (GL-121): start from a file the user already uploaded (the
  // new-dataset wizard), skipping the upload step.
  initialFile?: File
  initialRecordsKey?: string
  // GL-138: with `initialFile`, a way back out of the import (to the new-dataset
  // wizard's schema step) until the first commit. Resolves to an error message
  // to show, or null once the caller has moved on.
  onBack?: () => Promise<string | null>
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
  if (column?.type === 'json') {
    return <JsonFixInput value={value} disabled={disabled} onChange={onChange} />
  }
  if (column?.type === 'date') {
    // Same editor as the grid; the API applies the same date rules to fixes.
    return <DateInput value={typeof value === 'string' ? value : ''} disabled={disabled} onChange={onChange} />
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

export function ImportWizard({
  datasetId,
  columns,
  onClose,
  onImported,
  initialFile,
  initialRecordsKey,
  onBack,
}: ImportWizardProps) {
  const activeColumns = columns.filter((c) => !c.archived)
  const columnsByKey = new Map(activeColumns.map((c) => [c.key, c]))

  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [sourceColumns, setSourceColumns] = useState<string[]>([])
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [sampleRows, setSampleRows] = useState<ImportSampleRow[]>([])
  // JSON/YAML: where the records came from, and the choice when ambiguous.
  const [recordsKey, setRecordsKey] = useState<string | undefined>(undefined)
  const [ignoredKeys, setIgnoredKeys] = useState<string[]>([])
  const [recordsKeyChoices, setRecordsKeyChoices] = useState<string[] | null>(null)
  const [totalRows, setTotalRows] = useState(0)
  const [blankSkipped, setBlankSkipped] = useState(0)
  const [report, setReport] = useState<ImportValidationReport | null>(null)
  // The user ticked "import the valid rows anyway" on the wrong-schema banner.
  const [confirmedAnyway, setConfirmedAnyway] = useState(false)
  const [validCount, setValidCount] = useState(0)
  const [errors, setErrors] = useState<ImportValidationError[]>([])
  const [fixes, setFixes] = useState<Record<number, Record<string, unknown>>>({})
  const [activeSkip, setActiveSkip] = useState<Set<number>>(new Set())
  const [handledRows, setHandledRows] = useState<Set<number>>(new Set())
  const [totals, setTotals] = useState({ imported: 0, skipped: 0 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  // True once a commit round succeeded: rows exist, so Back is no longer safe.
  const [committed, setCommitted] = useState(false)
  // Remounts the file input so the same file can be picked again after Back.
  const [uploadKey, setUploadKey] = useState(0)

  const compat = mappingCompat(sourceColumns, mapping, activeColumns)
  const mappingBlock =
    compat.matched === 0
      ? 'Map at least one column of the file to a schema column.'
      : compat.requiredMissing.length > 0
        ? `No column of the file is mapped to ${labelList(compat.requiredMissing)} (required). Map one, or change the schema first.`
        : null
  // Half or more of the checked rows fail: probably the wrong schema (GL-133).
  const wrongSchema = !committed && report !== null && report.checked_rows > 0 && report.failed_rows * 2 >= report.checked_rows
  const shownRows = new Set(errors.map((e) => e.row)).size

  function showPreview(preview: ImportPreviewResponse) {
    setSourceColumns(preview.columns)
    setMapping(preview.suggested_mapping)
    setSampleRows(preview.sample_rows)
    setTotalRows(preview.total_rows)
    setBlankSkipped(preview.blank_skipped)
    setRecordsKey(preview.records_key ?? undefined)
    setIgnoredKeys(preview.ignored_keys ?? [])
    setRecordsKeyChoices(null)
    setStep('mapping')
  }

  async function loadPreview(chosen: File, key?: string) {
    setFile(chosen)
    setBusy(true)
    setError(null)
    try {
      showPreview(await previewImport(datasetId, chosen, undefined, key))
    } catch (err) {
      const candidates = candidatesOf(err)
      if (candidates && candidates.length > 0) {
        setRecordsKeyChoices(candidates)
        setError(RECORDS_LIST_QUESTION)
      } else {
        setError(err instanceof ApiError ? err.message : 'Could not read file')
      }
    } finally {
      setBusy(false)
    }
  }

  const started = useRef(false)
  useEffect(() => {
    if (!initialFile || started.current) return
    started.current = true
    setRecordsKey(initialRecordsKey)
    void loadPreview(initialFile, initialRecordsKey)
    // Run once, for the file passed in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function backOut() {
    if (!onBack) return
    setBusy(true)
    setError(null)
    const message = await onBack()
    setBusy(false)
    if (message) setError(message)
  }

  // Leaving the import is offered only before anything was imported.
  const backOutButton =
    initialFile && onBack && !committed ? (
      <button type="button" disabled={busy} onClick={() => void backOut()}>
        Back to schema
      </button>
    ) : null

  function handleFile(chosen: File) {
    setRecordsKey(undefined)
    setRecordsKeyChoices(null)
    void loadPreview(chosen)
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
      const preview = await previewImport(datasetId, file, mapping, recordsKey)
      setValidCount(preview.validation?.valid ?? 0)
      setErrors(preview.validation?.errors ?? [])
      setReport(preview.validation)
      setConfirmedAnyway(false)
      setBlankSkipped(preview.blank_skipped)
      setStep('review')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not validate mapping')
    } finally {
      setBusy(false)
    }
  }

  // Back clears what depends on the later step. A leftover fix would replace
  // the file's value for that row and column under a different mapping.
  function backToMapping() {
    setFixes({})
    setActiveSkip(new Set())
    setErrors([])
    setValidCount(0)
    setReport(null)
    setConfirmedAnyway(false)
    setError(null)
    setStep('mapping')
  }

  function backToUpload() {
    setMapping({})
    setRecordsKey(undefined)
    setRecordsKeyChoices(null)
    setIgnoredKeys([])
    setSourceColumns([])
    setSampleRows([])
    setTotalRows(0)
    setBlankSkipped(0)
    setFile(null)
    setError(null)
    setUploadKey((k) => k + 1)
    setStep('upload')
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
      const result = await commitImport(datasetId, file, mapping, [...effectiveSkip], fixes, recordsKey)
      const remainingRows = new Set(result.errors.map((e) => e.row))
      const importedThisRound = new Set<number>()
      for (let row = 1; row <= totalRows; row += 1) {
        if (!effectiveSkip.has(row) && !remainingRows.has(row)) importedThisRound.add(row)
      }
      setCommitted(true)
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
            <p>Upload a CSV, XLSX, JSON, JSONL or YAML file to import rows.</p>
            <input
              key={uploadKey}
              type="file"
              accept={ACCEPTED}
              disabled={busy}
              onChange={(event) => {
                const chosen = event.target.files?.[0]
                if (chosen) handleFile(chosen)
              }}
            />
            {recordsKeyChoices && file && (
              <label className="records-key-choice">
                Which list holds your data?{' '}
                <select
                  value=""
                  disabled={busy}
                  onChange={(event) => {
                    if (event.target.value === '') return
                    setRecordsKey(event.target.value)
                    void loadPreview(file, event.target.value)
                  }}
                >
                  <option value="">Choose…</option>
                  {recordsKeyChoices.map((key) => (
                    <option key={key} value={key}>
                      {key}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {backOutButton}
          </div>
        )}

        {step === 'mapping' && (
          <div>
            {recordsKey && (
              <p className="hint">
                Rows from <code>{recordsKey}</code>
                {ignoredKeys.length > 0 && <>; ignored: {ignoredKeys.join(', ')}</>}
              </p>
            )}
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
            <div className="import-compat">
              <p>
                {compat.matched} of {compat.total} file column{compat.total === 1 ? '' : 's'} matched.
              </p>
              {compat.ignored.length > 0 && (
                <p className="hint">Not imported (no column selected): {compat.ignored.join(', ')}</p>
              )}
              {compat.unfilled.length > 0 && (
                <p className="hint">Schema columns left empty: {labelList(compat.unfilled)}</p>
              )}
              {mappingBlock && <p className="field-error">{mappingBlock}</p>}
            </div>
            <p>
              {totalRows} row{totalRows === 1 ? '' : 's'} detected
              {blankSkipped > 0 && ` (${blankSkipped} blank line${blankSkipped === 1 ? '' : 's'} will be skipped)`}.
              Preview of the first {sampleRows.length}:
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
                        <td key={c}>{showValue(r.values[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!initialFile && (
              <button type="button" disabled={busy} onClick={backToUpload}>
                Back
              </button>
            )}
            {backOutButton}{' '}
            <button type="button" disabled={busy || mappingBlock !== null} onClick={() => void confirmMapping()}>
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
                  {errors.length > 0 &&
                    ` ${committed || !report ? errors.length : report.error_total} error${(committed || !report ? errors.length : report.error_total) === 1 ? '' : 's'} to resolve.`}
                  {blankSkipped > 0 && ` ${blankSkipped} blank line${blankSkipped === 1 ? '' : 's'} skipped.`}
                </p>
                {wrongSchema && report && (
                  <div className="notice warning-list" role="alert">
                    <p>
                      This looks like the wrong schema: {report.failed_rows} of {report.checked_rows} rows fail
                      validation. Go back to change the mapping, choose another file, or close this window. The schema
                      of an empty dataset can be changed from its Schema tab.
                    </p>
                    <label>
                      <input
                        type="checkbox"
                        checked={confirmedAnyway}
                        onChange={(event) => setConfirmedAnyway(event.target.checked)}
                      />{' '}
                      Import the valid rows anyway
                    </label>
                  </div>
                )}
                {!committed && report && report.by_column.length > 0 && (
                  <ul className="import-by-column">
                    {report.by_column.map((c) => (
                      <li key={c.column}>
                        {columnsByKey.get(c.column)?.label ?? (c.column || 'Whole record')}: {c.rows} of{' '}
                        {report.checked_rows} row{report.checked_rows === 1 ? '' : 's'}, {c.reason}
                      </li>
                    ))}
                  </ul>
                )}
                {!committed && report && report.failed_rows > shownRows && (
                  <p className="hint">
                    Showing the first {shownRows} of {report.failed_rows} rows with errors. Fix or skip these and import;
                    the rest are listed afterwards.
                  </p>
                )}
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
                            <td>{col?.label ?? (e.column || 'Whole record')}</td>
                            <td>{e.reason}</td>
                            <td>
                              {col ? (
                                <FixInput
                                  column={col}
                                  value={fixes[e.row]?.[e.column]}
                                  disabled={skipped}
                                  onChange={(value) => setFix(e.row, e.column, value)}
                                />
                              ) : (
                                '—'
                              )}
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
                {!committed && (
                  <>
                    <button type="button" disabled={busy} onClick={backToMapping}>
                      Back
                    </button>{' '}
                  </>
                )}
                <button
                  type="button"
                  disabled={busy || (wrongSchema && !confirmedAnyway)}
                  onClick={() => void confirmImport()}
                >
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
