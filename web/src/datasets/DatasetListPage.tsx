// Home page (GL-2-5; Phase 4, GL-119): collections first, then the datasets
// that aren't in any collection ("unfiled"). Editors and admins can create a
// collection, import a collection bundle, and create an unfiled dataset from
// a file (the same wizard as a collection's "Add datasets", without a store).
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createCollection, listCollections, listDatasets } from '../api/client'
import type { Collection, Dataset } from '../api/types'
import { ImportCollectionDialog } from '../collections/BundleDialogs'
import { NameDialog, errorMessage, useCanEdit } from '../collections/dialogs'
import { NewDatasetWizard } from '../collections/NewDatasetWizard'

export function DatasetListPage() {
  const navigate = useNavigate()
  const canEdit = useCanEdit()
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [datasets, setDatasets] = useState<Dataset[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'new-collection' | 'import' | 'new-dataset' | null>(null)

  const reload = useCallback(() => {
    Promise.all([listCollections(), listDatasets({ unfiled: true })])
      .then(([c, d]) => {
        setCollections(c)
        setDatasets(d)
      })
      .catch((err) => setError(errorMessage(err, 'Failed to load datasets')))
  }, [])

  useEffect(reload, [reload])

  if (error) return <p className="error">{error}</p>
  if (collections === null || datasets === null) return <p>Loading datasets…</p>

  return (
    <div className="home-page">
      <section>
        <div className="page-toolbar">
          <h2>Collections</h2>
          {canEdit && (
            <span className="toolbar-actions">
              <button type="button" onClick={() => setDialog('new-collection')}>
                New collection
              </button>
              <button type="button" onClick={() => setDialog('import')}>
                Import collection
              </button>
            </span>
          )}
        </div>
        {collections.length === 0 ? (
          <p>No collections yet.</p>
        ) : (
          <ul className="dataset-list">
            {collections.map((c) => (
              <li key={c.id}>
                <Link to={`/collections/${c.id}`}>{c.name}</Link>{' '}
                <span className="field-hint">
                  {c.dataset_count} dataset{c.dataset_count === 1 ? '' : 's'}
                </span>
                {c.description && <p className="dataset-description">{c.description}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="page-toolbar">
          <h2>Datasets not in a collection</h2>
          {canEdit && (
            <button type="button" onClick={() => setDialog('new-dataset')}>
              New dataset
            </button>
          )}
        </div>
        {datasets.length === 0 ? (
          <p>No datasets outside collections.</p>
        ) : (
          <ul className="dataset-list">
            {datasets.map((dataset) => (
              <li key={dataset.id}>
                <Link to={`/datasets/${dataset.id}`}>{dataset.name}</Link>
                {dataset.description && <p className="dataset-description">{dataset.description}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {dialog === 'new-collection' && (
        <NameDialog
          title="New collection"
          confirmLabel="Create collection"
          withDescription
          onSubmit={async (name, description) => {
            try {
              const created = await createCollection(name, description)
              navigate(`/collections/${created.id}`)
              return null
            } catch (err) {
              return errorMessage(err, 'Could not create the collection')
            }
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'import' && <ImportCollectionDialog onClose={() => setDialog(null)} />}
      {dialog === 'new-dataset' && (
        <NewDatasetWizard
          collection={null}
          onChanged={reload}
          onClose={() => {
            setDialog(null)
            reload()
          }}
        />
      )}
    </div>
  )
}
