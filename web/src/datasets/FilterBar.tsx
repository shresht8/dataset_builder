// Filter controls + saved views (design §4). All-native form controls so
// the whole bar is keyboard-operable via Tab/arrows without custom wiring.
import type { RowStatus, User } from '../api/types'
import { BUILTIN_VIEWS, filtersEqual, type SavedView, type ViewFilters } from './views'

interface FilterBarProps {
  filters: ViewFilters
  missingRequired: boolean
  searchInput: string
  onSearchInputChange: (value: string) => void
  onFiltersChange: (filters: ViewFilters) => void
  onMissingRequiredChange: (value: boolean) => void
  customViews: SavedView[]
  onApplyView: (view: SavedView) => void
  onSaveView: () => void
  onDeleteView: (id: string) => void
  isAdmin: boolean
  users: User[] | null
}

export function FilterBar({
  filters,
  missingRequired,
  searchInput,
  onSearchInputChange,
  onFiltersChange,
  onMissingRequiredChange,
  customViews,
  onApplyView,
  onSaveView,
  onDeleteView,
  isAdmin,
  users,
}: FilterBarProps) {
  function viewButton(view: SavedView, deletable: boolean) {
    const active = filtersEqual(filters, view.filters, missingRequired, view.missingRequired)
    return (
      <span key={view.id} className="view-item">
        <button type="button" className={active ? 'view-btn active' : 'view-btn'} onClick={() => onApplyView(view)}>
          {view.name}
        </button>
        {deletable && (
          <button type="button" className="view-delete" aria-label={`Delete view ${view.name}`} onClick={() => onDeleteView(view.id)}>
            &times;
          </button>
        )}
      </span>
    )
  }

  return (
    <div className="filter-bar">
      <div className="view-buttons">
        {BUILTIN_VIEWS.map((view) => viewButton(view, false))}
        {customViews.map((view) => viewButton(view, true))}
        <button type="button" onClick={onSaveView}>
          + Save view
        </button>
      </div>
      <div className="filter-controls">
        <label>
          Status
          <select
            value={filters.status ?? ''}
            onChange={(event) =>
              onFiltersChange({
                ...filters,
                status: event.target.value === '' ? undefined : (event.target.value as RowStatus),
              })
            }
          >
            <option value="">Any</option>
            <option value="draft">Draft</option>
            <option value="needs_review">Needs review</option>
            <option value="approved">Approved</option>
          </select>
        </label>
        <label>
          Assignee
          <select
            value={filters.assignee ?? ''}
            onChange={(event) =>
              onFiltersChange({ ...filters, assignee: event.target.value === '' ? undefined : event.target.value })
            }
          >
            <option value="">Anyone</option>
            <option value="me">Assigned to me</option>
            {isAdmin &&
              (users ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Search
          <input
            type="text"
            value={searchInput}
            onChange={(event) => onSearchInputChange(event.target.value)}
            placeholder="Search text fields"
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={missingRequired}
            onChange={(event) => onMissingRequiredChange(event.target.checked)}
          />
          Missing required only
        </label>
      </div>
    </div>
  )
}
