// Dataset picker (GL-2-5). The grid itself is GL-2-6; clicking a dataset
// routes to a placeholder page for now.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, listDatasets } from '../api/client'
import type { Dataset } from '../api/types'

export function DatasetListPage() {
  const [datasets, setDatasets] = useState<Dataset[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listDatasets()
      .then(setDatasets)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load datasets'))
  }, [])

  if (error) return <p className="error">{error}</p>
  if (datasets === null) return <p>Loading datasets…</p>

  if (datasets.length === 0) {
    return <p>No datasets yet.</p>
  }

  return (
    <ul className="dataset-list">
      {datasets.map((dataset) => (
        <li key={dataset.id}>
          <Link to={`/datasets/${dataset.id}`}>{dataset.name}</Link>
          {dataset.description && <p className="dataset-description">{dataset.description}</p>}
        </li>
      ))}
    </ul>
  )
}
