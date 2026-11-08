// A collection (Phase 4, GL-119): a folder tree on the left, the current
// folder's subfolders and datasets on the right, and the collection's schema
// store on a second tab. Editors and admins get the write actions: add
// datasets, new / rename / delete folder (delete only when empty), move a
// dataset or remove it from the collection, and delete the collection (only
// when it holds no datasets). Everyone can browse and export.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { createFolder, deleteCollection, deleteFolder, getCollection, renameFolder } from '../api/client'
import type { CollectionTree, Folder, TreeDataset } from '../api/types'
import { ConfirmDialog } from '../datasets/ConfirmDialog'
import { ExportDialog } from './BundleDialogs'
import { MoveDialog, NameDialog, TypedConfirmDialog, errorMessage, useCanEdit } from './dialogs'
import { NewDatasetWizard } from './NewDatasetWizard'
import { StorePanel } from './StorePanel'

function FolderTree({
  tree,
  current,
  onSelect,
}: {
  tree: CollectionTree
  current: string | null
  onSelect: (folderId: string | null) => void
}) {
  const children = useMemo(() => {
    const map = new Map<string | null, Folder[]>()
    for (const folder of tree.folders) {
      const list = map.get(folder.parent_id) ?? []
      list.push(folder)
      map.set(folder.parent_id, list)
    }
    for (const list of map.values()) list.sort((a, b) => a.name.localeCompare(b.name))
    return map
  }, [tree])

  function render(parent: string | null) {
    const list = children.get(parent)
    if (!list) return null
    return (
      <ul>
        {list.map((folder) => (
          <li key={folder.id}>
            <button
              type="button"
              className={`link-button${current === folder.id ? ' tree-active' : ''}`}
              onClick={() => onSelect(folder.id)}
            >
              📁 {folder.name}
            </button>
            {render(folder.id)}
          </li>
        ))}
      </ul>
    )
  }

  return (
    <nav className="folder-tree" aria-label="Folders">
      <button
        type="button"
        className={`link-button${current === null ? ' tree-active' : ''}`}
        onClick={() => onSelect(null)}
      >
        {tree.name}
      </button>
      {render(null)}
    </nav>
  )
}

export function CollectionPage() {
  const { collectionId } = useParams<{ collectionId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const canEdit = useCanEdit()
  const folderId = searchParams.get('folder')
  const [tree, setTree] = useState<CollectionTree | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'datasets' | 'store'>('datasets')
  const [dialog, setDialog] = useState<
    | { kind: 'new-folder' }
    | { kind: 'rename-folder'; folder: Folder }
    | { kind: 'delete-folder'; folder: Folder }
    | { kind: 'delete-collection' }
    | { kind: 'move'; dataset: TreeDataset; unfile: boolean }
    | { kind: 'add-datasets' }
    | { kind: 'export' }
    | null
  >(null)

  const reload = useCallback(() => {
    if (!collectionId) return
    getCollection(collectionId)
      .then(setTree)
      .catch((err) => setError(errorMessage(err, 'Failed to load collection')))
  }, [collectionId])

  useEffect(reload, [reload])

  const folderPath = useMemo(() => {
    if (!tree) return []
    const byId = new Map(tree.folders.map((f) => [f.id, f]))
    const path: Folder[] = []
    let id = folderId
    while (id) {
      const folder = byId.get(id)
      if (!folder) break
      path.unshift(folder)
      id = folder.parent_id
    }
    return path
  }, [tree, folderId])

  if (error) return <p className="error">{error}</p>
  if (!tree || !collectionId) return <p>Loading collection…</p>

  const currentFolder = folderPath.at(-1) ?? null
  const subfolders = tree.folders
    .filter((f) => f.parent_id === (currentFolder?.id ?? null))
    .sort((a, b) => a.name.localeCompare(b.name))
  const datasets = tree.datasets.filter((d) => d.folder_id === (currentFolder?.id ?? null))
  const folderEmpty =
    currentFolder !== null &&
    !tree.folders.some((f) => f.parent_id === currentFolder.id) &&
    !tree.datasets.some((d) => d.folder_id === currentFolder.id)

  function select(id: string | null) {
    setSearchParams(id ? { folder: id } : {})
  }

  return (
    <div className="collection-page">
      <div className="page-toolbar">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Home</Link> / <button type="button" className="link-button" onClick={() => select(null)}>{tree.name}</button>
          {folderPath.map((f) => (
            <span key={f.id}>
              {' / '}
              <button type="button" className="link-button" onClick={() => select(f.id)}>
                {f.name}
              </button>
            </span>
          ))}
        </nav>
        <span className="toolbar-actions">
          {canEdit && (
            <button type="button" onClick={() => setDialog({ kind: 'add-datasets' })}>
              Add datasets
            </button>
          )}
          <button type="button" onClick={() => setDialog({ kind: 'export' })}>
            Export
          </button>
          {canEdit && (
            <button
              type="button"
              className="danger"
              disabled={tree.dataset_count > 0}
              title={tree.dataset_count > 0 ? 'Only a collection with no datasets can be deleted' : undefined}
              onClick={() => setDialog({ kind: 'delete-collection' })}
            >
              Delete collection
            </button>
          )}
        </span>
      </div>
      {tree.description && <p className="dataset-description">{tree.description}</p>}

      <div className="tab-bar" role="tablist">
        <button type="button" role="tab" className={tab === 'datasets' ? 'tab-active' : undefined} onClick={() => setTab('datasets')}>
          Datasets
        </button>
        <button type="button" role="tab" className={tab === 'store' ? 'tab-active' : undefined} onClick={() => setTab('store')}>
          Store
        </button>
      </div>

      {tab === 'store' && <StorePanel collectionId={collectionId} canEdit={canEdit} />}

      {tab === 'datasets' && (
        <div className="collection-layout">
          <FolderTree tree={tree} current={currentFolder?.id ?? null} onSelect={select} />
          <section className="collection-content">
            {canEdit && (
              <div className="folder-actions">
                <button type="button" onClick={() => setDialog({ kind: 'new-folder' })}>
                  New folder
                </button>
                {currentFolder && (
                  <>
                    <button type="button" onClick={() => setDialog({ kind: 'rename-folder', folder: currentFolder })}>
                      Rename folder
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={!folderEmpty}
                      title={folderEmpty ? undefined : 'Only an empty folder (no subfolders or datasets) can be deleted'}
                      onClick={() => setDialog({ kind: 'delete-folder', folder: currentFolder })}
                    >
                      Delete folder
                    </button>
                  </>
                )}
              </div>
            )}
            {subfolders.length === 0 && datasets.length === 0 && <p>This folder is empty.</p>}
            <ul className="dataset-list">
              {subfolders.map((f) => (
                <li key={f.id}>
                  <button type="button" className="link-button" onClick={() => select(f.id)}>
                    📁 {f.name}
                  </button>
                </li>
              ))}
            </ul>
            {datasets.length > 0 && (
              <table className="import-table">
                <thead>
                  <tr>
                    <th>Dataset</th>
                    <th>Store schema</th>
                    <th>Rows</th>
                    <th>Versions</th>
                    {canEdit && <th />}
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((d) => (
                    <tr key={d.id}>
                      <td>
                        <Link to={`/datasets/${d.id}`}>{d.name}</Link>
                      </td>
                      <td>{d.store_schema_name}</td>
                      <td>{d.row_count}</td>
                      <td>{d.version_count}</td>
                      {canEdit && (
                        <td className="row-actions">
                          <button type="button" onClick={() => setDialog({ kind: 'move', dataset: d, unfile: false })}>
                            Move
                          </button>
                          <button type="button" onClick={() => setDialog({ kind: 'move', dataset: d, unfile: true })}>
                            Remove from collection
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}

      {dialog?.kind === 'new-folder' && (
        <NameDialog
          title={currentFolder ? `New folder in ${currentFolder.name}` : `New folder in ${tree.name}`}
          confirmLabel="Create folder"
          onSubmit={async (name) => {
            try {
              await createFolder(collectionId, name, currentFolder?.id ?? null)
              reload()
              return null
            } catch (err) {
              return errorMessage(err, 'Could not create the folder')
            }
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'rename-folder' && (
        <NameDialog
          title={`Rename folder ${dialog.folder.name}`}
          confirmLabel="Rename"
          initialName={dialog.folder.name}
          onSubmit={async (name) => {
            try {
              await renameFolder(dialog.folder.id, name)
              reload()
              return null
            } catch (err) {
              return errorMessage(err, 'Could not rename the folder')
            }
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'delete-folder' && (
        <ConfirmDialog
          title={`Delete folder ${dialog.folder.name}?`}
          confirmLabel="Delete folder"
          onConfirm={async () => {
            try {
              await deleteFolder(dialog.folder.id)
              select(dialog.folder.parent_id)
              reload()
              return null
            } catch (err) {
              return errorMessage(err, 'Could not delete the folder')
            }
          }}
          onClose={() => setDialog(null)}
        >
          <p>The folder is empty.</p>
        </ConfirmDialog>
      )}
      {dialog?.kind === 'delete-collection' && (
        <TypedConfirmDialog
          title={`Delete collection ${tree.name}?`}
          name={tree.name}
          confirmLabel="Delete collection"
          onConfirm={async () => {
            try {
              await deleteCollection(collectionId)
              navigate('/')
              return null
            } catch (err) {
              return errorMessage(err, 'Could not delete the collection')
            }
          }}
          onClose={() => setDialog(null)}
        >
          <p>It holds no datasets. Its folders and schema store are deleted with it. This can't be undone.</p>
        </TypedConfirmDialog>
      )}
      {dialog?.kind === 'move' && (
        <MoveDialog
          dataset={{ ...dialog.dataset, collection_id: tree.id }}
          unfile={dialog.unfile}
          onMoved={() => {
            setDialog(null)
            reload()
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'add-datasets' && (
        <NewDatasetWizard
          collection={tree}
          initialFolderId={currentFolder?.id ?? null}
          onChanged={reload}
          onClose={() => {
            setDialog(null)
            reload()
          }}
        />
      )}
      {dialog?.kind === 'export' && (
        <ExportDialog collectionId={collectionId} name={tree.name} onClose={() => setDialog(null)} />
      )}
    </div>
  )
}
