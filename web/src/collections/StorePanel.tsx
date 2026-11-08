// A collection's schema store (Phase 4, GL-119 list, GL-120 editing). Each
// entry shows its columns and the datasets using it. Editors can add a schema,
// duplicate one, and edit or delete an unused one: an entry in use by any
// dataset is frozen (D6), so changing it means duplicate -> edit the copy ->
// apply the copy from the dataset's Schema panel.
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  createStoreEntry,
  deleteStoreEntry,
  duplicateStoreEntry,
  listStore,
  updateStoreEntry,
} from '../api/client'
import type { StoreEntry } from '../api/types'
import { ConfirmDialog } from '../datasets/ConfirmDialog'
import { SchemaEditor, columnProblems, toEditable, toInput, type EditableColumn } from '../schema/SchemaEditor'
import { Dialog, NameDialog, errorMessage } from './dialogs'

interface StoreEntryEditorProps {
  collectionId: string
  entry: StoreEntry | null // null = a new entry
  onSaved: (entry: StoreEntry) => void
  onClose: () => void
}

export function StoreEntryEditor({ collectionId, entry, onSaved, onClose }: StoreEntryEditorProps) {
  const [name, setName] = useState(entry?.name ?? '')
  const [columns, setColumns] = useState<EditableColumn[]>(() => toEditable(entry?.columns ?? []))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const invalid = Object.keys(columnProblems(columns)).length > 0 || name.trim() === ''

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const saved = entry
        ? await updateStoreEntry(collectionId, entry.id, { name: name.trim(), columns: toInput(columns) })
        : await createStoreEntry(collectionId, name.trim(), toInput(columns))
      onSaved(saved)
    } catch (err) {
      setError(errorMessage(err, 'Save failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog title={entry ? `Edit store schema ${entry.name}` : 'New store schema'} onClose={onClose} wide>
      <label className="stacked-form">
        Name
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <SchemaEditor columns={columns} onChange={setColumns} serverError={error} />
      <div className="conflict-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="button" disabled={busy || invalid} onClick={() => void save()}>
          Save
        </button>
      </div>
    </Dialog>
  )
}

export function StorePanel({ collectionId, canEdit }: { collectionId: string; canEdit: boolean }) {
  const [entries, setEntries] = useState<StoreEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<StoreEntry | 'new' | null>(null)
  const [duplicating, setDuplicating] = useState<StoreEntry | null>(null)
  const [deleting, setDeleting] = useState<StoreEntry | null>(null)

  const reload = useCallback(() => {
    listStore(collectionId)
      .then(setEntries)
      .catch((err) => setError(errorMessage(err, 'Could not load the store')))
  }, [collectionId])

  useEffect(reload, [reload])

  function toggle(id: string) {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (error) return <p className="error">{error}</p>
  if (entries === null) return <p>Loading store…</p>

  return (
    <div className="store-panel">
      <p className="field-hint">
        Every dataset in this collection uses one of these schemas. A schema in use can't be changed: duplicate it,
        edit the copy, then apply the copy from the dataset's Schema panel.
      </p>
      {canEdit && (
        <button type="button" onClick={() => setEditing('new')}>
          New schema
        </button>
      )}
      {entries.length === 0 && <p>No schemas yet. Adding a dataset adds its schema here.</p>}
      <ul className="store-list">
        {entries.map((entry) => {
          const used = entry.used_by.length > 0
          return (
            <li key={entry.id}>
              <div className="store-entry-header">
                <button type="button" className="link-button" onClick={() => toggle(entry.id)} aria-expanded={open.has(entry.id)}>
                  {open.has(entry.id) ? '▾' : '▸'} <strong>{entry.name}</strong>
                </button>
                <span>
                  {entry.columns.length} column{entry.columns.length === 1 ? '' : 's'}
                </span>
                <span className="store-used-by">
                  {used ? (
                    <>
                      Used by{' '}
                      {entry.used_by.map((u, i) => (
                        <span key={u.id}>
                          {i > 0 && ', '}
                          <Link to={`/datasets/${u.id}`}>{u.path}</Link>
                        </span>
                      ))}
                    </>
                  ) : (
                    'Not used'
                  )}
                </span>
                {canEdit && (
                  <span className="store-actions">
                    <button type="button" onClick={() => setDuplicating(entry)}>
                      Duplicate
                    </button>
                    <button
                      type="button"
                      disabled={used}
                      title={used ? `Used by ${entry.used_by.length} dataset(s) — duplicate to change` : undefined}
                      onClick={() => setEditing(entry)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={used}
                      title={used ? `Used by: ${entry.used_by.map((u) => u.path).join(', ')}` : undefined}
                      onClick={() => setDeleting(entry)}
                    >
                      Delete
                    </button>
                  </span>
                )}
              </div>
              {open.has(entry.id) && (
                <div className="store-entry-body">
                  {used && canEdit && (
                    <p className="hint">
                      Used by {entry.used_by.length} dataset{entry.used_by.length === 1 ? '' : 's'} — duplicate to
                      change.
                    </p>
                  )}
                  <SchemaEditor columns={toEditable(entry.columns)} />
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {editing && (
        <StoreEntryEditor
          collectionId={collectionId}
          entry={editing === 'new' ? null : editing}
          onSaved={() => {
            setEditing(null)
            reload()
          }}
          onClose={() => setEditing(null)}
        />
      )}
      {duplicating && (
        <NameDialog
          title={`Duplicate ${duplicating.name}`}
          confirmLabel="Duplicate"
          initialName={`${duplicating.name}-copy`}
          onSubmit={async (name) => {
            try {
              await duplicateStoreEntry(collectionId, duplicating.id, name)
              reload()
              return null
            } catch (err) {
              return errorMessage(err, 'Duplicate failed')
            }
          }}
          onClose={() => setDuplicating(null)}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`Delete store schema ${deleting.name}?`}
          confirmLabel="Delete schema"
          onConfirm={async () => {
            try {
              await deleteStoreEntry(collectionId, deleting.id)
              reload()
              return null
            } catch (err) {
              return errorMessage(err, 'Delete failed')
            }
          }}
          onClose={() => setDeleting(null)}
        >
          <p>No dataset uses it. This can't be undone.</p>
        </ConfirmDialog>
      )}
    </div>
  )
}
