// The dataset Schema panel (Phase 4, GL-120). Reverses the 2026-10-10
// "schemas are CLI-only" decision (D15).
//   - Unfiled dataset: edit the columns directly and save (PUT …/schema).
//   - Collection dataset: its schema is a store entry. "Change schema" picks
//     another entry; "Duplicate & edit" copies the current entry, edits the
//     copy and applies it (D6), then offers to delete the old entry if nothing
//     uses it any more.
// Either way, columns that would be archived are listed for confirmation
// first; a refused write (e.g. duplicate key values) shows the server's
// message. Viewers and annotators get a read-only view.
import { useEffect, useState } from 'react'
import {
  applyStoreSchema,
  deleteStoreEntry,
  duplicateStoreEntry,
  getSchema,
  listStore,
  putSchema,
  updateStoreEntry,
} from '../api/client'
import type { Column, Dataset, StoreEntry } from '../api/types'
import { Dialog, NameDialog, errorMessage } from '../collections/dialogs'
import { SchemaEditor, columnProblems, toEditable, toInput, type EditableColumn } from '../schema/SchemaEditor'
import { ConfirmDialog } from './ConfirmDialog'

type Mode = 'view' | 'edit' | 'pick' | 'copy'

interface PendingWrite {
  archived: string[]
  run: () => Promise<string | null>
}

interface SchemaPanelProps {
  dataset: Dataset
  canEdit: boolean
  onClose: () => void
  onChanged: () => void
}

export function SchemaPanel({ dataset, canEdit, onClose, onChanged }: SchemaPanelProps) {
  const [current, setCurrent] = useState<Column[] | null>(null)
  const [storeName, setStoreName] = useState<string | null>(null)
  const [storeId, setStoreId] = useState<string | null>(null)
  const [entries, setEntries] = useState<StoreEntry[]>([])
  const [mode, setMode] = useState<Mode>('view')
  const [columns, setColumns] = useState<EditableColumn[]>([])
  const [copy, setCopy] = useState<StoreEntry | null>(null)
  const [picked, setPicked] = useState<StoreEntry | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingWrite | null>(null)
  const [naming, setNaming] = useState(false)
  const [offerDelete, setOfferDelete] = useState<StoreEntry | null>(null)
  const inCollection = dataset.collection_id !== null

  function load() {
    getSchema(dataset.id)
      .then((schema) => {
        setCurrent(schema.columns)
        setStoreName(schema.store_schema?.name ?? null)
        setStoreId(schema.store_schema?.id ?? null)
      })
      .catch((err) => setError(errorMessage(err, 'Could not load the schema')))
    if (dataset.collection_id) {
      listStore(dataset.collection_id)
        .then(setEntries)
        .catch(() => setEntries([]))
    }
  }

  useEffect(load, [dataset.id, dataset.collection_id])

  function archivedBy(next: { key: string }[]): string[] {
    const keys = new Set(next.map((c) => c.key))
    return (current ?? []).map((c) => c.key).filter((k) => !keys.has(k))
  }

  // Confirm first when columns would be archived; otherwise write straight away.
  async function write(next: { key: string }[], run: () => Promise<string | null>) {
    const archived = archivedBy(next)
    if (archived.length > 0) {
      setPending({ archived, run })
      return
    }
    const message = await run()
    if (message) setError(message)
  }

  async function finish(previousEntryId: string | null) {
    setMode('view')
    setCopy(null)
    setPicked(null)
    load()
    onChanged()
    if (previousEntryId && dataset.collection_id) {
      const fresh = await listStore(dataset.collection_id)
      setEntries(fresh)
      const old = fresh.find((e) => e.id === previousEntryId)
      if (old && old.used_by.length === 0) setOfferDelete(old)
    }
  }

  async function saveUnfiled(): Promise<string | null> {
    try {
      await putSchema(dataset.id, toInput(columns))
      setNotice('Schema saved.')
      await finish(null)
      return null
    } catch (err) {
      return errorMessage(err, 'Save failed')
    }
  }

  async function applyEntry(entry: StoreEntry): Promise<string | null> {
    const previous = storeId
    try {
      await applyStoreSchema(dataset.id, entry.id)
      setNotice(`Now using store schema "${entry.name}".`)
      await finish(previous !== entry.id ? previous : null)
      return null
    } catch (err) {
      return errorMessage(err, 'Apply failed')
    }
  }

  async function saveCopyAndApply(): Promise<string | null> {
    if (!copy || !dataset.collection_id) return null
    try {
      const updated = await updateStoreEntry(dataset.collection_id, copy.id, { columns: toInput(columns) })
      return await applyEntry(updated)
    } catch (err) {
      return errorMessage(err, 'Save failed')
    }
  }

  const invalid = Object.keys(columnProblems(columns)).length > 0

  return (
    <Dialog title={`Schema of ${dataset.path}`} onClose={onClose} wide>
      {notice && <p className="hint">{notice}</p>}
      {error && mode === 'view' && <p className="field-error">{error}</p>}
      {current === null && !error && <p>Loading schema…</p>}

      {current && mode === 'view' && (
        <>
          {inCollection && (
            <p>
              Store schema: <strong>{storeName}</strong>
            </p>
          )}
          <SchemaEditor columns={toEditable(current)} />
          {canEdit && (
            <div className="conflict-actions">
              {inCollection ? (
                <>
                  <button type="button" onClick={() => { setError(null); setMode('pick') }}>
                    Change schema
                  </button>
                  <button type="button" onClick={() => { setError(null); setNaming(true) }}>
                    Duplicate &amp; edit
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setColumns(toEditable(current))
                    setError(null)
                    setMode('edit')
                  }}
                >
                  Edit schema
                </button>
              )}
            </div>
          )}
        </>
      )}

      {mode === 'edit' && (
        <>
          <SchemaEditor columns={columns} onChange={setColumns} serverError={error} />
          <div className="conflict-actions">
            <button type="button" onClick={() => setMode('view')}>
              Cancel
            </button>
            <button type="button" disabled={invalid} onClick={() => void write(toInput(columns), saveUnfiled)}>
              Save schema
            </button>
          </div>
        </>
      )}

      {mode === 'pick' && (
        <>
          <p>Pick a schema from the collection's store:</p>
          <ul className="store-pick">
            {entries.map((entry) => (
              <li key={entry.id}>
                <label>
                  <input
                    type="radio"
                    name="apply-entry"
                    checked={picked?.id === entry.id}
                    disabled={entry.id === storeId}
                    onChange={() => setPicked(entry)}
                  />
                  {entry.name} ({entry.columns.length} columns){entry.id === storeId ? ' — current' : ''}
                </label>
              </li>
            ))}
          </ul>
          {picked && <SchemaEditor columns={toEditable(picked.columns)} />}
          {error && <p className="field-error">{error}</p>}
          <div className="conflict-actions">
            <button type="button" onClick={() => setMode('view')}>
              Cancel
            </button>
            <button
              type="button"
              disabled={!picked}
              onClick={() => picked && void write(picked.columns, () => applyEntry(picked))}
            >
              Apply
            </button>
          </div>
        </>
      )}

      {mode === 'copy' && copy && (
        <>
          <p className="field-hint">
            Editing <strong>{copy.name}</strong>, a copy of {storeName}. Saving applies it to this dataset.
          </p>
          <SchemaEditor columns={columns} onChange={setColumns} serverError={error} />
          <div className="conflict-actions">
            <button type="button" onClick={() => setMode('view')}>
              Cancel (keep the copy in the store)
            </button>
            <button type="button" disabled={invalid} onClick={() => void write(toInput(columns), saveCopyAndApply)}>
              Save &amp; apply
            </button>
          </div>
        </>
      )}

      {naming && storeId && dataset.collection_id && (
        <NameDialog
          title={`Duplicate ${storeName}`}
          confirmLabel="Duplicate"
          initialName={`${storeName}-2`}
          onSubmit={async (name) => {
            try {
              const created = await duplicateStoreEntry(dataset.collection_id!, storeId, name)
              setCopy(created)
              setColumns(toEditable(created.columns))
              setMode('copy')
              return null
            } catch (err) {
              return errorMessage(err, 'Duplicate failed')
            }
          }}
          onClose={() => setNaming(false)}
        />
      )}
      {pending && (
        <ConfirmDialog
          title={`Archive ${pending.archived.length} column(s)?`}
          confirmLabel="Archive and save"
          onConfirm={async () => {
            const message = await pending.run()
            if (message === null) setPending(null)
            return message
          }}
          onClose={() => setPending(null)}
        >
          <p>
            These columns leave the schema: <strong>{pending.archived.join(', ')}</strong>. Their values stay in
            existing rows and old versions.
          </p>
        </ConfirmDialog>
      )}
      {offerDelete && dataset.collection_id && (
        <ConfirmDialog
          title={`Delete the old store schema ${offerDelete.name}?`}
          confirmLabel="Delete it"
          onConfirm={async () => {
            try {
              await deleteStoreEntry(dataset.collection_id!, offerDelete.id)
              return null
            } catch (err) {
              return errorMessage(err, 'Delete failed')
            }
          }}
          onClose={() => setOfferDelete(null)}
        >
          <p>No dataset uses it any more. Cancel keeps it in the store.</p>
        </ConfirmDialog>
      )}
    </Dialog>
  )
}
