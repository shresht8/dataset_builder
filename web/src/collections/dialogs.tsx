// Small dialogs for collections (Phase 4, GL-119): a name prompt (new
// collection / folder, rename, duplicate a store schema), moving a dataset,
// and deleting a collection by typing its name. Same style as ConfirmDialog;
// Escape closes them.
import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ApiError, getCollection, listCollections, moveDataset } from '../api/client'
import type { Collection, CollectionTree, Dataset } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { nameProblem } from '../names'

const RANK = { viewer: 0, annotator: 1, editor: 2, admin: 3 } as const

/** Editors and admins can change structure, schemas and imports (D16). */
export function useCanEdit(): boolean {
  const { user } = useAuth()
  return RANK[user?.role ?? 'viewer'] >= RANK.editor
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function Dialog({
  title,
  children,
  onClose,
  wide,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  wide?: boolean
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }
  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className={wide ? 'import-wizard' : 'conflict-dialog'} role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-header">
          <h3>{title}</h3>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

interface NameDialogProps {
  title: string
  confirmLabel: string
  initialName?: string
  withDescription?: boolean
  hint?: ReactNode
  // Resolves to an error message to show inline, or null when done.
  onSubmit: (name: string, description: string | null) => Promise<string | null>
  onClose: () => void
}

export function NameDialog({ title, confirmLabel, initialName = '', withDescription, hint, onSubmit, onClose }: NameDialogProps) {
  const [name, setName] = useState(initialName)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const problem = nameProblem(name.trim())

  async function submit() {
    if (problem) return
    setBusy(true)
    setError(null)
    const message = await onSubmit(name.trim(), withDescription && description.trim() ? description.trim() : null)
    setBusy(false)
    if (message === null) onClose()
    else setError(message)
  }

  return (
    <Dialog title={title} onClose={onClose}>
      <form
        className="stacked-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        {hint}
        <label>
          Name
          <input autoFocus value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        {problem ? (
          <span className="field-error">{problem}</span>
        ) : (
          <span className="field-hint">Lowercase letters, digits, - and _; starts with a letter or digit.</span>
        )}
        {withDescription && (
          <label>
            Description
            <input value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
        )}
        {error && <p className="field-error">{error}</p>}
        <div className="conflict-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={busy || name.trim() === '' || problem !== null}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** Indented `path` labels for a collection's folders, parents before children. */
export function folderOptions(tree: CollectionTree): { id: string; label: string; depth: number }[] {
  const children = new Map<string | null, CollectionTree['folders']>()
  for (const folder of tree.folders) {
    const list = children.get(folder.parent_id) ?? []
    list.push(folder)
    children.set(folder.parent_id, list)
  }
  const out: { id: string; label: string; depth: number }[] = []
  function walk(parent: string | null, prefix: string, depth: number) {
    for (const folder of [...(children.get(parent) ?? [])].sort((a, b) => a.name.localeCompare(b.name))) {
      const label = prefix ? `${prefix}/${folder.name}` : folder.name
      out.push({ id: folder.id, label, depth })
      walk(folder.id, label, depth + 1)
    }
  }
  walk(null, '', 0)
  return out
}

interface MoveDialogProps {
  dataset: Pick<Dataset, 'id' | 'name' | 'collection_id' | 'folder_id'>
  // Pre-select "Not in a collection" (the "Remove from collection" action).
  unfile?: boolean
  onMoved: (dataset: Dataset) => void
  onClose: () => void
}

export function MoveDialog({ dataset, unfile, onMoved, onClose }: MoveDialogProps) {
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [collectionId, setCollectionId] = useState<string>(unfile ? '' : dataset.collection_id ?? '')
  const [tree, setTree] = useState<CollectionTree | null>(null)
  const [folderId, setFolderId] = useState<string>(unfile ? '' : dataset.folder_id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [moved, setMoved] = useState<Dataset | null>(null)

  useEffect(() => {
    listCollections()
      .then(setCollections)
      .catch((err) => setError(errorMessage(err, 'Could not load collections')))
  }, [])

  useEffect(() => {
    if (!collectionId) return
    let cancelled = false
    getCollection(collectionId)
      .then((t) => !cancelled && setTree(t))
      .catch((err) => setError(errorMessage(err, 'Could not load folders')))
    return () => {
      cancelled = true
    }
  }, [collectionId])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const result = await moveDataset(dataset.id, collectionId || null, collectionId && folderId ? folderId : null)
      setMoved(result)
    } catch (err) {
      setError(errorMessage(err, 'Move failed'))
    } finally {
      setBusy(false)
    }
  }

  if (moved) {
    const enteredCollection = moved.collection_id !== null && moved.collection_id !== dataset.collection_id
    return (
      <Dialog title={`Moved ${dataset.name}`} onClose={() => onMoved(moved)}>
        <p>
          Now at <code>{moved.path}</code>.
        </p>
        {enteredCollection && moved.store_schema && (
          <p className="hint">
            {moved.store_schema.created
              ? `Its schema was added to the collection's store as "${moved.store_schema.name}".`
              : `Its schema matches the store schema "${moved.store_schema.name}", which it now uses.`}
          </p>
        )}
        {moved.collection_id === null && <p className="hint">It's no longer in a collection. Its data is kept.</p>}
        <div className="conflict-actions">
          <button type="button" onClick={() => onMoved(moved)}>
            Done
          </button>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog title={`Move ${dataset.name}`} onClose={onClose}>
      <div className="stacked-form">
        <label>
          Collection
          <select
            value={collectionId}
            onChange={(event) => {
              setCollectionId(event.target.value)
              setFolderId('')
              setTree(null)
            }}
          >
            <option value="">Not in a collection</option>
            {(collections ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {collectionId && (
          <label>
            Folder
            <select value={folderId} onChange={(event) => setFolderId(event.target.value)} disabled={!tree}>
              <option value="">(collection root)</option>
              {tree &&
                folderOptions(tree).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
            </select>
          </label>
        )}
        {collectionId === '' && dataset.collection_id && (
          <p className="hint">The dataset leaves its collection. Its rows, versions and schema are kept.</p>
        )}
        {error && <p className="field-error">{error}</p>}
        <div className="conflict-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="button" disabled={busy || collections === null} onClick={() => void submit()}>
            Move
          </button>
        </div>
      </div>
    </Dialog>
  )
}

export function TypedConfirmDialog({
  title,
  name,
  children,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string
  name: string
  children: ReactNode
  confirmLabel: string
  onConfirm: () => Promise<string | null>
  onClose: () => void
}) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function confirm() {
    setBusy(true)
    const message = await onConfirm()
    setBusy(false)
    if (message !== null) setError(message)
  }

  return (
    <Dialog title={title} onClose={onClose}>
      {children}
      <label className="confirm-name">
        <span>
          Type <code>{name}</code> to confirm
        </span>
        <input autoFocus value={typed} onChange={(event) => setTyped(event.target.value)} />
      </label>
      {error && <p className="field-error">{error}</p>}
      <div className="conflict-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="danger" disabled={busy || typed !== name} onClick={() => void confirm()}>
          {confirmLabel}
        </button>
      </div>
    </Dialog>
  )
}
