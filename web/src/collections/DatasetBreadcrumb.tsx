// `Home / collection / folder / … / dataset` above a dataset (Phase 4, GL-119).
// Each part links back: the collection, or the collection opened at a folder.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getCollection } from '../api/client'
import type { CollectionTree, Dataset } from '../api/types'

export function DatasetBreadcrumb({ dataset }: { dataset: Dataset | null }) {
  const [tree, setTree] = useState<CollectionTree | null>(null)

  useEffect(() => {
    if (!dataset?.collection_id) {
      setTree(null)
      return
    }
    getCollection(dataset.collection_id)
      .then(setTree)
      .catch(() => setTree(null))
  }, [dataset?.collection_id, dataset?.folder_id])

  if (!dataset) return <Link to="/">&larr; Datasets</Link>
  if (!dataset.collection_id || !tree) {
    return (
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/">Home</Link> / <strong>{dataset.name}</strong>
      </nav>
    )
  }

  const byId = new Map(tree.folders.map((f) => [f.id, f]))
  const folders = []
  let id = dataset.folder_id
  while (id) {
    const folder = byId.get(id)
    if (!folder) break
    folders.unshift(folder)
    id = folder.parent_id
  }

  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      <Link to="/">Home</Link> / <Link to={`/collections/${tree.id}`}>{tree.name}</Link>
      {folders.map((f) => (
        <span key={f.id}>
          {' / '}
          <Link to={`/collections/${tree.id}?folder=${f.id}`}>{f.name}</Link>
        </span>
      ))}
      {' / '}
      <strong>{dataset.name}</strong>
    </nav>
  )
}
