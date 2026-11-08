// New-dataset wizard (Phase 4, GL-121): raw files -> datasets, one at a time.
// The user drops one or more files (the import formats); each becomes a queue
// item processed in turn:
//   1. details: name (from the file name) and folder;
//   2. schema: Infer from the file, or pick from the collection's store
//      (editable either way; editing a store schema edits a copy);
//   3. create: the API resolves the schema against the store (D7), so a new
//      schema is added and the next file can pick it;
//   4. import: the existing mapping -> review -> commit steps, on the same file.
// Without a collection (home page "New dataset") the store step is skipped and
// the dataset is unfiled. Skipping or cancelling an item after its dataset was
// created offers to delete the still-empty dataset.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError,
  createDataset,
  deleteDataset,
  getSchema,
  inferSchema,
  listStore,
} from '../api/client'
import type { Column, CollectionTree, Dataset, StoreEntry } from '../api/types'
import { ConfirmDialog } from '../datasets/ConfirmDialog'
import { ImportWizard } from '../datasets/ImportWizard'
import { SchemaEditor, columnProblems, toEditable, toInput, type EditableColumn } from '../schema/SchemaEditor'
import { Dialog, errorMessage, folderOptions } from './dialogs'

const ACCEPTED = '.csv,.xlsx,.json,.jsonl,.ndjson,.yaml,.yml'

type ItemStatus = 'pending' | 'in progress' | 'imported' | 'skipped' | 'failed'

interface QueueItem {
  file: File
  status: ItemStatus
  dataset?: Dataset
  note?: string
}

type Step = 'files' | 'details' | 'schema' | 'import' | 'summary'

/** `My File (2).CSV` -> `my-file-2`: fits ^[a-z0-9][a-z0-9_-]{0,99}$. */
export function nameFromFile(fileName: string): string {
  const stem = fileName.replace(/\.[^.]+$/, '').toLowerCase()
  const cleaned = stem.replace(/[^a-z0-9_-]+/g, '-').replace(/^[-_]+/, '').replace(/-+$/, '')
  return (cleaned || 'dataset').slice(0, 100)
}

function candidatesOf(err: unknown): string[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null
  const body = err.body as { records_key_candidates?: unknown } | null
  return Array.isArray(body?.records_key_candidates) ? (body.records_key_candidates as string[]) : null
}

interface NewDatasetWizardProps {
  collection: CollectionTree | null
  initialFolderId?: string | null
  onClose: () => void
  // Called whenever a dataset was created (so the page can refresh).
  onChanged: () => void
}

export function NewDatasetWizard({ collection, initialFolderId = null, onClose, onChanged }: NewDatasetWizardProps) {
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [index, setIndex] = useState(0)
  const [step, setStep] = useState<Step>('files')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Per-item state.
  const [name, setName] = useState('')
  const [folderId, setFolderId] = useState<string>(initialFolderId ?? '')
  const [recordsKey, setRecordsKey] = useState<string | undefined>(undefined)
  const [keyChoices, setKeyChoices] = useState<string[] | null>(null)
  const [tab, setTab] = useState<'infer' | 'store'>('infer')
  const [columns, setColumns] = useState<EditableColumn[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [recordCount, setRecordCount] = useState<number | null>(null)
  const [entries, setEntries] = useState<StoreEntry[]>([])
  const [picked, setPicked] = useState<StoreEntry | null>(null)
  const [editingCopy, setEditingCopy] = useState(false)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [importColumns, setImportColumns] = useState<Column[] | null>(null)
  const [imported, setImported] = useState(false)
  const [offerDelete, setOfferDelete] = useState(false)
  const deletedRef = useRef(false)

  const item = queue[index]

  function patchItem(patch: Partial<QueueItem>) {
    setQueue((prev) => prev.map((q, i) => (i === index ? { ...q, ...patch } : q)))
  }

  function beginItem(i: number) {
    const next = queue[i]
    setIndex(i)
    setName(nameFromFile(next.file.name))
    setFolderId(initialFolderId ?? '')
    setRecordsKey(undefined)
    setKeyChoices(null)
    setTab('infer')
    setColumns([])
    setWarnings([])
    setRecordCount(null)
    setPicked(null)
    setEditingCopy(false)
    setOutcome(null)
    setImportColumns(null)
    setImported(false)
    setError(null)
    setQueue((prev) => prev.map((q, j) => (j === i ? { ...q, status: 'in progress' } : q)))
    setStep('details')
  }

  function advance() {
    const nextIndex = queue.findIndex((q, i) => i > index && q.status === 'pending')
    if (nextIndex === -1) setStep('summary')
    else beginItem(nextIndex)
  }

  async function runInfer(key?: string) {
    if (!item) return
    setBusy(true)
    setError(null)
    try {
      const result = await inferSchema(item.file, key)
      setColumns(toEditable(result.columns))
      setWarnings(result.warnings)
      setRecordCount(result.record_count)
      setKeyChoices(null)
    } catch (err) {
      const candidates = candidatesOf(err)
      if (candidates && candidates.length > 0) setKeyChoices(candidates)
      setError(errorMessage(err, 'Could not read the file'))
    } finally {
      setBusy(false)
    }
  }

  // Entering the schema step: infer once, and refresh the store (it grows as
  // datasets are created, so the next file can pick what was just added).
  useEffect(() => {
    if (step !== 'schema') return
    if (collection) {
      listStore(collection.id)
        .then(setEntries)
        .catch(() => setEntries([]))
    }
    if (columns.length === 0 && recordCount === null) void runInfer(recordsKey)
    // Only on entering the step.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step])

  async function create() {
    if (!item) return
    setBusy(true)
    setError(null)
    const usePicked = tab === 'store' && picked && !editingCopy
    try {
      const dataset = await createDataset({
        name: name.trim(),
        collection_id: collection?.id ?? null,
        folder_id: collection && folderId ? folderId : null,
        ...(usePicked ? { store_schema_id: picked.id } : { columns: toInput(columns) }),
      })
      patchItem({ dataset })
      if (dataset.store_schema) {
        setOutcome(
          dataset.store_schema.created
            ? `Added to store as "${dataset.store_schema.name}"`
            : `Using store schema "${dataset.store_schema.name}"`,
        )
      } else {
        setOutcome(`Created ${dataset.path}`)
      }
      setImportColumns((await getSchema(dataset.id)).columns)
      onChanged()
      setStep('import')
    } catch (err) {
      setError(errorMessage(err, 'Create failed'))
    } finally {
      setBusy(false)
    }
  }

  function skipItem() {
    if (item?.dataset) {
      setOfferDelete(true)
      return
    }
    patchItem({ status: 'skipped' })
    advance()
  }

  const schemaInvalid =
    tab === 'store' && !editingCopy ? picked === null : columns.length === 0 || Object.keys(columnProblems(columns)).length > 0

  // --- render -------------------------------------------------------------------

  if (step === 'import' && item?.dataset && importColumns) {
    return (
      <>
        {outcome && !offerDelete && <div className="wizard-banner hint">{outcome}</div>}
        {!offerDelete && (
          <ImportWizard
            datasetId={item.dataset.id}
            columns={importColumns}
            initialFile={item.file}
            initialRecordsKey={recordsKey}
            onImported={() => {
              setImported(true)
              patchItem({ status: 'imported' })
              onChanged()
            }}
            onClose={() => {
              if (imported) advance()
              else setOfferDelete(true)
            }}
          />
        )}
        {offerDelete && (
          <ConfirmDialog
            title={`Delete the empty dataset ${item.dataset.name}?`}
            confirmLabel="Delete dataset"
            onConfirm={async () => {
              try {
                await deleteDataset(item.dataset!.id)
                deletedRef.current = true
                onChanged()
                return null // ConfirmDialog then calls onClose, which moves on
              } catch (err) {
                return errorMessage(err, 'Delete failed')
              }
            }}
            onClose={() => {
              patchItem(
                deletedRef.current
                  ? { status: 'skipped', note: 'created, then deleted', dataset: undefined }
                  : { status: 'skipped', note: 'kept empty' },
              )
              deletedRef.current = false
              setOfferDelete(false)
              advance()
            }}
          >
            <p>Nothing was imported into it. Cancel keeps it (empty) and moves on.</p>
          </ConfirmDialog>
        )}
      </>
    )
  }

  const title = collection ? `Add datasets to ${collection.name}` : 'New dataset'

  return (
    <Dialog title={title} onClose={onClose} wide>
      {queue.length > 0 && step !== 'files' && (
        <ol className="wizard-queue">
          {queue.map((q, i) => (
            <li key={`${q.file.name}-${i}`} className={i === index && step !== 'summary' ? 'queue-active' : undefined}>
              {q.file.name} — {q.status}
              {q.note ? ` (${q.note})` : ''}
              {q.dataset && q.status === 'imported' && (
                <>
                  {' '}
                  <Link to={`/datasets/${q.dataset.id}`}>{q.dataset.path}</Link>
                </>
              )}
            </li>
          ))}
        </ol>
      )}
      {error && <p className="error">{error}</p>}

      {step === 'files' && (
        <div className="stacked-form">
          <p>
            Choose one or more files (CSV, XLSX, JSON, JSONL or YAML). Each becomes a dataset, one at a time: name it,
            define its schema, then import its rows.
          </p>
          <input
            type="file"
            multiple
            accept={ACCEPTED}
            aria-label="Data files"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              setQueue(files.map((file) => ({ file, status: 'pending' })))
            }}
          />
          {queue.length > 0 && (
            <p>
              {queue.length} file{queue.length === 1 ? '' : 's'} queued: {queue.map((q) => q.file.name).join(', ')}
            </p>
          )}
          <div className="conflict-actions">
            <button type="button" disabled={queue.length === 0} onClick={() => beginItem(0)}>
              Start
            </button>
          </div>
        </div>
      )}

      {step === 'details' && item && (
        <div className="stacked-form">
          <h4>{item.file.name}</h4>
          <label>
            Dataset name
            <input autoFocus value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <span className="field-hint">Lowercase letters, digits, - and _.</span>
          {collection && (
            <label>
              Folder
              <select value={folderId} onChange={(event) => setFolderId(event.target.value)}>
                <option value="">(collection root)</option>
                {folderOptions(collection).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="conflict-actions">
            <button type="button" onClick={skipItem}>
              Skip this file
            </button>
            <button type="button" disabled={name.trim() === ''} onClick={() => setStep('schema')}>
              Next: schema
            </button>
          </div>
        </div>
      )}

      {step === 'schema' && item && (
        <div>
          <h4>
            Schema for <code>{name}</code>
          </h4>
          {collection && (
            <div className="tab-bar" role="tablist">
              <button type="button" role="tab" className={tab === 'infer' ? 'tab-active' : undefined} onClick={() => setTab('infer')}>
                Infer from file
              </button>
              <button type="button" role="tab" className={tab === 'store' ? 'tab-active' : undefined} onClick={() => setTab('store')}>
                From store
              </button>
            </div>
          )}

          {keyChoices && (
            <label className="records-key-choice">
              Which list holds the records?{' '}
              <select
                value=""
                onChange={(event) => {
                  if (event.target.value === '') return
                  setRecordsKey(event.target.value)
                  void runInfer(event.target.value)
                }}
              >
                <option value="">Choose…</option>
                {keyChoices.map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </select>
            </label>
          )}

          {tab === 'infer' && (
            <>
              {recordCount !== null && (
                <p className="field-hint">
                  Drafted from {recordCount} record{recordCount === 1 ? '' : 's'}. Review the types, options and key
                  column before creating.
                </p>
              )}
              {warnings.length > 0 && (
                <ul className="notice warning-list">
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              )}
              <SchemaEditor columns={columns} onChange={setColumns} serverError={error} />
            </>
          )}

          {tab === 'store' && (
            <>
              {entries.length === 0 && <p>The store is empty. Infer a schema from the file instead.</p>}
              <ul className="store-pick">
                {entries.map((entry) => (
                  <li key={entry.id}>
                    <label>
                      <input
                        type="radio"
                        name="store-entry"
                        checked={picked?.id === entry.id}
                        onChange={() => {
                          setPicked(entry)
                          setEditingCopy(false)
                        }}
                      />
                      {entry.name} ({entry.columns.length} columns
                      {entry.used_by.length > 0 ? `, used by ${entry.used_by.length}` : ''})
                    </label>
                  </li>
                ))}
              </ul>
              {picked && !editingCopy && (
                <>
                  <SchemaEditor columns={toEditable(picked.columns)} />
                  <button
                    type="button"
                    onClick={() => {
                      setColumns(toEditable(picked.columns))
                      setEditingCopy(true)
                    }}
                  >
                    Edit a copy
                  </button>
                </>
              )}
              {picked && editingCopy && (
                <>
                  <p className="field-hint">
                    Editing a copy of {picked.name}. If you change it, creating the dataset adds it to the store as a new
                    schema.
                  </p>
                  <SchemaEditor columns={columns} onChange={setColumns} serverError={error} />
                </>
              )}
            </>
          )}

          <div className="conflict-actions">
            <button type="button" onClick={() => setStep('details')}>
              Back
            </button>
            <button type="button" onClick={skipItem}>
              Skip this file
            </button>
            <button type="button" disabled={busy || schemaInvalid} onClick={() => void create()}>
              Create dataset
            </button>
          </div>
        </div>
      )}

      {step === 'summary' && (
        <div>
          <p>
            Done: {queue.filter((q) => q.status === 'imported').length} imported,{' '}
            {queue.filter((q) => q.status === 'skipped').length} skipped.
          </p>
          <div className="conflict-actions">
            {collection ? <Link to={`/collections/${collection.id}`} onClick={onClose}>Back to {collection.name}</Link> : null}
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      )}
    </Dialog>
  )
}
