// Filters and saved views (design §4). Built-in views are code; custom
// named views persist per-dataset in localStorage (decision: browser-local,
// not a backend resource).
import type { RowStatus } from '../api/types'

export interface ViewFilters {
  status?: RowStatus
  assignee?: string // 'me' or a user id
  q?: string
}

export interface SavedView {
  id: string
  name: string
  filters: ViewFilters
  // Client-side only: rows missing a value for any required column (§4).
  missingRequired?: boolean
}

export const BUILTIN_VIEWS: SavedView[] = [
  { id: 'builtin:all', name: 'All rows', filters: {} },
  { id: 'builtin:my-queue', name: 'My queue', filters: { assignee: 'me' } },
  { id: 'builtin:needs-review', name: 'Needs review', filters: { status: 'needs_review' } },
  { id: 'builtin:missing-required', name: 'Missing required', filters: {}, missingRequired: true },
]

// Default view on open (§4: "the default view is my queue").
export const DEFAULT_VIEW = BUILTIN_VIEWS[1]

export function filtersEqual(
  a: ViewFilters,
  b: ViewFilters,
  aMissingRequired?: boolean,
  bMissingRequired?: boolean,
): boolean {
  return (
    (a.status ?? '') === (b.status ?? '') &&
    (a.assignee ?? '') === (b.assignee ?? '') &&
    (a.q ?? '') === (b.q ?? '') &&
    Boolean(aMissingRequired) === Boolean(bMissingRequired)
  )
}

function storageKey(datasetId: string): string {
  return `groundline.savedViews.${datasetId}`
}

export function loadCustomViews(datasetId: string): SavedView[] {
  try {
    const raw = localStorage.getItem(storageKey(datasetId))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as SavedView[]) : []
  } catch {
    return []
  }
}

export function saveCustomViews(datasetId: string, views: SavedView[]): void {
  localStorage.setItem(storageKey(datasetId), JSON.stringify(views))
}
