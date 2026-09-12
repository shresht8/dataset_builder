// Placeholder route for a single dataset. The typed grid (keyboard nav,
// cell editors, detail drawer) is built in GL-2-6.
import { Link, useParams } from 'react-router-dom'

export function DatasetPage() {
  const { datasetId } = useParams<{ datasetId: string }>()

  return (
    <div>
      <p>
        <Link to="/">&larr; Datasets</Link>
      </p>
      <p>Grid for dataset {datasetId} is not built yet (GL-2-6).</p>
    </div>
  )
}
