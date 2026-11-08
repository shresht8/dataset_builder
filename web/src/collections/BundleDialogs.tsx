// Collection export / import (Phase 4, GL-123). Export downloads a bundle
// zip of draft rows (default) or each dataset's latest version, listing the
// datasets a `latest` export skipped (no version yet). Import uploads a
// bundle, previews it (tree, store, row counts, errors; the name is editable
// and a taken one is flagged), and commits it as a new collection.
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, exportCollection, importBundle, previewBundle } from '../api/client'
import type { BundleReport } from '../api/types'
import { Dialog, errorMessage } from './dialogs'

export function ExportDialog({ collectionId, name, onClose }: { collectionId: string; name: string; onClose: () => void }) {
  const [source, setSource] = useState<'draft' | 'latest'>('draft')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [skipped, setSkipped] = useState<string[] | null>(null)

  async function download() {
    setBusy(true)
    setError(null)
    try {
      const result = await exportCollection(collectionId, source)
      const url = URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${name}${source === 'latest' ? '-latest' : ''}.zip`
      link.click()
      URL.revokeObjectURL(url)
      setSkipped(result.skipped)
    } catch (err) {
      setError(errorMessage(err, 'Export failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog title={`Export ${name}`} onClose={onClose}>
      <div className="stacked-form">
        <label className="checkbox-label">
          <input type="radio" name="source" checked={source === 'draft'} onChange={() => setSource('draft')} />
          Draft rows — the current rows of every dataset
        </label>
        <label className="checkbox-label">
          <input type="radio" name="source" checked={source === 'latest'} onChange={() => setSource('latest')} />
          Latest published versions — datasets without a version are skipped
        </label>
        {error && <p className="field-error">{error}</p>}
        {skipped !== null && (
          <p className="hint">
            Downloaded.
            {skipped.length > 0 && <> Skipped (no version yet): {skipped.join(', ')}</>}
          </p>
        )}
        <div className="conflict-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
          <button type="button" disabled={busy} onClick={() => void download()}>
            Download zip
          </button>
        </div>
      </div>
    </Dialog>
  )
}

export function ImportCollectionDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [report, setReport] = useState<BundleReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  async function preview(chosen: File, override?: string) {
    setBusy(true)
    setError(null)
    setNameError(null)
    try {
      const result = await previewBundle(chosen, override || undefined)
      setReport(result)
      setName(result.name)
      if (result.name_taken) setNameError(`A collection named '${result.name}' already exists — choose another name`)
    } catch (err) {
      setReport(null)
      setError(errorMessage(err, 'Could not read the bundle'))
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const created = await importBundle(file, name)
      onClose()
      navigate(`/collections/${created.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setNameError(err.message)
      else setError(errorMessage(err, 'Import failed'))
    } finally {
      setBusy(false)
    }
  }

  const blocked = !report || !report.ok || report.name_taken || name !== report.name

  return (
    <Dialog title="Import collection" onClose={onClose} wide>
      <div className="stacked-form">
        <p>Upload a collection bundle (.zip) exported from Groundline. It's imported as a new collection; rows start as draft.</p>
        <input
          type="file"
          accept=".zip"
          aria-label="Bundle zip"
          disabled={busy}
          onChange={(event) => {
            const chosen = event.target.files?.[0] ?? null
            setFile(chosen)
            if (chosen) void preview(chosen)
          }}
        />
        {error && <p className="field-error">{error}</p>}
      </div>

      {report && file && (
        <div className="bundle-preview">
          <div className="stacked-form">
            <label>
              Collection name
              <span className="inline-field">
                <input value={name} onChange={(event) => setName(event.target.value)} aria-invalid={nameError ? true : undefined} />
                <button type="button" disabled={busy || name === report.name} onClick={() => void preview(file, name)}>
                  Check name
                </button>
              </span>
            </label>
            {nameError && <p className="field-error">{nameError}</p>}
          </div>
          {report.errors.length > 0 && (
            <ul className="field-error">
              {report.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <h4>Folders</h4>
          <p>{report.folders.length > 0 ? report.folders.join(', ') : '(none)'}</p>
          <h4>Store schemas</h4>
          <ul>
            {report.store.map((s) => (
              <li key={s.name}>
                {s.name} — {s.columns} columns{s.error && <span className="field-error"> {s.error}</span>}
              </li>
            ))}
          </ul>
          <h4>Datasets</h4>
          <table className="import-table">
            <thead>
              <tr>
                <th>Path</th>
                <th>Store schema</th>
                <th>Rows</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {report.datasets.map((d) => (
                <tr key={d.path}>
                  <td>{d.path}</td>
                  <td>{d.store_schema}</td>
                  <td>{d.rows}</td>
                  <td>
                    {d.error_count === 0
                      ? '—'
                      : d.errors.map((e) => (
                          <div key={`${e.row}:${e.column}`} className="field-error">
                            row {e.row}
                            {e.column ? ` ${e.column}` : ''}: {e.reason}
                          </div>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!report.ok && <p className="field-error">Fix the errors in the bundle before importing.</p>}
          <div className="conflict-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="button" disabled={busy || blocked} onClick={() => void commit()}>
              Import as {name}
            </button>
          </div>
        </div>
      )}
    </Dialog>
  )
}
