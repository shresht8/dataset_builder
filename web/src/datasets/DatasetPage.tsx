// The typed annotation grid (design §4, GL-2-6): one row per case, columns
// from the dataset schema, keyboard-first navigation and editing, a detail
// drawer for long_text and json. Filters/saved views, row status/assignee,
// comments, and the 409 conflict UI are the workflow layer added in GL-2-7.
// Editing follows the role (GL-3.5-14): viewers get no edit affordances, and
// an existing row's key value can only be changed by editors and admins.
import { createColumnHelper, flexRender, tableFeatures, useTable } from '@tanstack/react-table'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  createRow,
  getSchema,
  listRows,
  listUsers,
  patchRow,
  type RowPatchPayload,
} from '../api/client'
import type { Column, Role, Row, User } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { CellDisplay, CellEditor, MultiSelectEditor } from './Cell'
import { CommentsPanel } from './CommentsPanel'
import { ConflictDialog } from './ConflictDialog'
import { DetailDrawer } from './DetailDrawer'
import { FilterBar } from './FilterBar'
import { ImportWizard } from './ImportWizard'
import { VersionsPanel } from './VersionsPanel'
import { AssigneeControl, StatusControl } from './Workflow'
import {
  BUILTIN_VIEWS,
  DEFAULT_VIEW,
  filtersEqual,
  loadCustomViews,
  saveCustomViews,
  type SavedView,
  type ViewFilters,
} from './views'

interface CellCoord {
  rowId: string
  colKey: string
}

interface DisplayRow {
  id: string
  data: Record<string, unknown>
}

interface ConflictState {
  rowId: string
  payload: RowPatchPayload
  theirs: Row
}

const NEW_ROW_ID = 'new'
const ALL_ROWS_VIEW = BUILTIN_VIEWS.find((v) => v.id === 'builtin:all')!

// The grid uses no sorting/filtering/selection — just the default (core) row
// model — so the feature set is empty (§4: keyboard nav/editing is all custom).
const gridFeatures = tableFeatures({})
const columnHelper = createColumnHelper<typeof gridFeatures, DisplayRow>()

const ROLE_RANK: Record<Role, number> = { viewer: 0, annotator: 1, editor: 2, admin: 3 }

// Mirrors the backend's is_empty (validation.py): for json only null is empty.
function isEmptyValue(column: Column, value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (column.type === 'json') return false
  if (typeof value === 'string' && value.trim() === '') return true
  if (Array.isArray(value) && value.length === 0) return true
  return false
}

// Mirrors the backend's required check (rows.py:_validate_row_data) so the
// pending new row is only POSTed once it would actually pass validation.
function canCreate(columns: Column[], data: Record<string, unknown>): boolean {
  return columns.every((col) => col.archived || !col.required || !isEmptyValue(col, data[col.key]))
}

function isMissingRequired(columns: Column[], data: Record<string, unknown>): boolean {
  return columns.some((col) => !col.archived && col.required && isEmptyValue(col, data[col.key]))
}

// A 409 from PATCH carries the row's current state (rev conflict); other 409s,
// like a duplicate key value, carry only a `detail` message.
function isRowBody(body: unknown): body is Row {
  return typeof body === 'object' && body !== null && typeof (body as Row).rev === 'number'
}

const opensDrawer = (column: Column) => column.type === 'long_text' || column.type === 'json'

function KeyIcon() {
  return (
    <svg className="key-icon" viewBox="0 0 16 16" width="12" height="12" aria-label="Key column" role="img">
      <title>Key column: identifies each row</title>
      <circle cx="5" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 8h7M12 8v3M14.5 8v2" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  )
}

function initialDraft(column: Column, value: unknown): unknown {
  if (column.type === 'number') return typeof value === 'number' ? value : null
  if (column.type === 'multi_select') return Array.isArray(value) ? value : []
  if (column.type === 'boolean') return Boolean(value)
  return typeof value === 'string' ? value : ''
}

export function DatasetPage() {
  const { datasetId } = useParams<{ datasetId: string }>()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const rank = ROLE_RANK[user?.role ?? 'viewer']
  const canAnnotate = rank >= ROLE_RANK.annotator
  const canImport = rank >= ROLE_RANK.editor
  const [columns, setColumns] = useState<Column[] | null>(null)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [users, setUsers] = useState<User[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pendingData, setPendingData] = useState<Record<string, unknown> | null>(null)
  const [activeCell, setActiveCell] = useState<CellCoord | null>(null)
  const [editingCell, setEditingCell] = useState<CellCoord | null>(null)
  const [draft, setDraft] = useState<unknown>(null)
  const [drawerRowId, setDrawerRowId] = useState<string | null>(null)
  const [drawerFocusKey, setDrawerFocusKey] = useState<string | undefined>(undefined)
  const [commentsRowId, setCommentsRowId] = useState<string | null>(null)
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  // Per-cell save errors shown inline, e.g. a duplicate key value (409).
  const [cellErrors, setCellErrors] = useState<Record<string, string>>({})
  const [filters, setFilters] = useState<ViewFilters>(DEFAULT_VIEW.filters)
  const [missingRequired, setMissingRequired] = useState(Boolean(DEFAULT_VIEW.missingRequired))
  const [searchInput, setSearchInput] = useState(DEFAULT_VIEW.filters.q ?? '')
  const [customViews, setCustomViews] = useState<SavedView[]>([])
  const [importOpen, setImportOpen] = useState(false)
  const [versionsOpen, setVersionsOpen] = useState(false)
  const cellRefs = useRef(new Map<string, HTMLTableCellElement>())

  useEffect(() => {
    if (!datasetId) return
    getSchema(datasetId)
      .then((schema) => setColumns(schema.columns))
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load dataset'))
  }, [datasetId])

  useEffect(() => {
    if (!datasetId) return
    listRows(datasetId, { status: filters.status, assignee: filters.assignee, q: filters.q })
      .then(setRows)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load dataset'))
  }, [datasetId, filters.status, filters.assignee, filters.q])

  useEffect(() => {
    if (!datasetId) return
    setCustomViews(loadCustomViews(datasetId))
  }, [datasetId])

  useEffect(() => {
    if (!isAdmin) {
      setUsers(null)
      return
    }
    listUsers()
      .then(setUsers)
      .catch(() => setUsers(null))
  }, [isAdmin])

  // Debounce free-text search before it hits the backend q= param.
  useEffect(() => {
    const handle = setTimeout(() => {
      setFilters((prev) => (prev.q ?? '') === searchInput ? prev : { ...prev, q: searchInput || undefined })
    }, 300)
    return () => clearTimeout(handle)
  }, [searchInput])

  const orderedColumns = useMemo(
    () => (columns ?? []).filter((col) => !col.archived).sort((a, b) => a.order - b.order),
    [columns],
  )
  const drawerColumns = useMemo(() => orderedColumns.filter(opensDrawer), [orderedColumns])
  const columnsByKey = useMemo(
    () => new Map(orderedColumns.map((col) => [col.key, col])),
    [orderedColumns],
  )
  const visibleRows = useMemo(() => {
    if (!missingRequired) return rows ?? []
    return (rows ?? []).filter((row) => isMissingRequired(orderedColumns, row.data))
  }, [rows, missingRequired, orderedColumns])
  const displayRows: DisplayRow[] = useMemo(() => {
    return pendingData !== null ? [...visibleRows, { id: NEW_ROW_ID, data: pendingData }] : visibleRows
  }, [visibleRows, pendingData])

  // Required columns are enforced on create (rows.py), so the pending row
  // can't be POSTed blank; auto-create it the moment it becomes valid.
  useEffect(() => {
    if (!datasetId || pendingData === null || columns === null) return
    if (!canCreate(orderedColumns, pendingData)) return
    let cancelled = false
    createRow(datasetId, pendingData)
      .then((created) => {
        if (cancelled) return
        setRows((prev) => [...(prev ?? []), created])
        setPendingData(null)
        setActiveCell((current) =>
          current && current.rowId === NEW_ROW_ID ? { rowId: created.id, colKey: current.colKey } : current,
        )
      })
      .catch((err) => {
        if (cancelled) return
        const keyColumn = orderedColumns.find((col) => col.is_key)
        if (err instanceof ApiError && err.status === 409 && keyColumn) {
          setCellError({ rowId: NEW_ROW_ID, colKey: keyColumn.key }, err.message)
        } else {
          setNotice(err instanceof ApiError ? err.message : 'Could not create row')
        }
      })
    return () => {
      cancelled = true
    }
  }, [datasetId, pendingData, columns, orderedColumns])

  // Keep the active cell valid as the visible row set changes (filters,
  // saved views, row creation); reset to the first cell when it no longer is.
  useEffect(() => {
    setActiveCell((prev) => {
      if (prev && displayRows.some((r) => r.id === prev.rowId) && orderedColumns.some((c) => c.key === prev.colKey)) {
        return prev
      }
      if (orderedColumns.length > 0 && displayRows.length > 0) {
        return { rowId: displayRows[0].id, colKey: orderedColumns[0].key }
      }
      return null
    })
  }, [displayRows, orderedColumns])

  // Roving tabindex: move real DOM focus to the active cell whenever nothing
  // else (an inline editor, the drawer, or a panel/dialog) currently owns it.
  useEffect(() => {
    if (editingCell || drawerRowId || commentsRowId || conflict || !activeCell) return
    cellRefs.current.get(`${activeCell.rowId}:${activeCell.colKey}`)?.focus()
  }, [activeCell, editingCell, drawerRowId, commentsRowId, conflict])

  function getCellValue(coord: CellCoord): unknown {
    return displayRows.find((row) => row.id === coord.rowId)?.data[coord.colKey]
  }

  // Re-fetch rows under the current filters (e.g. after an import, GL-2-9).
  function reloadRows() {
    if (!datasetId) return
    listRows(datasetId, { status: filters.status, assignee: filters.assignee, q: filters.q })
      .then(setRows)
      .catch((err) => setNotice(err instanceof ApiError ? err.message : 'Failed to refresh rows'))
  }

  function setCellError(coord: CellCoord, message: string | null) {
    const cacheKey = `${coord.rowId}:${coord.colKey}`
    setCellErrors((prev) => {
      if (message === null) {
        if (!(cacheKey in prev)) return prev
        const next = { ...prev }
        delete next[cacheKey]
        return next
      }
      return { ...prev, [cacheKey]: message }
    })
  }

  // Optimistic-locking PATCH (§4): on 409 the conflicting current row is
  // surfaced via the conflict dialog instead of being silently applied.
  // Resolves to an error message for any other failure (null on success).
  async function applyRowPatch(rowId: string, payload: RowPatchPayload, revOverride?: number): Promise<string | null> {
    if (!datasetId) return null
    const rev = revOverride ?? (rows ?? []).find((r) => r.id === rowId)?.rev
    if (rev === undefined) return null
    try {
      const updated = await patchRow(datasetId, rowId, rev, payload)
      setRows((prev) => (prev ?? []).map((r) => (r.id === rowId ? updated : r)))
      setConflict(null)
      return null
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && isRowBody(err.body)) {
        setConflict({ rowId, payload, theirs: err.body })
        return null
      }
      return err instanceof ApiError ? err.message : 'Save failed'
    }
  }

  async function patchOrNotify(rowId: string, payload: RowPatchPayload, revOverride?: number) {
    setNotice(await applyRowPatch(rowId, payload, revOverride))
  }

  async function saveField(rowId: string, colKey: string, value: unknown) {
    const error = await applyRowPatch(rowId, { data: { [colKey]: value } })
    // A duplicate key value is shown on the cell itself; anything else as a notice.
    const isKeyClash = error !== null && Boolean(columnsByKey.get(colKey)?.is_key)
    // The API names the other row's id; in a narrow cell the key alone reads better.
    setCellError({ rowId, colKey }, isKeyClash ? error.replace(/ \(row [0-9a-f-]+\)$/, '') : null)
    setNotice(isKeyClash ? null : error)
  }

  function saveFromDrawer(rowId: string, colKey: string, value: unknown): Promise<string | null> {
    if (rowId === NEW_ROW_ID) {
      setPendingData((prev) => ({ ...(prev ?? {}), [colKey]: value }))
      return Promise.resolve(null)
    }
    return applyRowPatch(rowId, { data: { [colKey]: value } })
  }

  function handleStatusChange(rowId: string, status: string) {
    void patchOrNotify(rowId, { status: status as Row['status'] })
  }

  function handleAssigneeChange(rowId: string, assignee: string | null) {
    void patchOrNotify(rowId, { assignee })
  }

  function handleTakeTheirs() {
    if (!conflict) return
    setRows((prev) => (prev ?? []).map((r) => (r.id === conflict.rowId ? conflict.theirs : r)))
    setConflict(null)
  }

  function handleRetryMine() {
    if (!conflict) return
    void patchOrNotify(conflict.rowId, conflict.payload, conflict.theirs.rev)
  }

  function saveCell(coord: CellCoord, value: unknown) {
    if (coord.rowId === NEW_ROW_ID) {
      setCellError(coord, null)
      setPendingData((prev) => ({ ...(prev ?? {}), [coord.colKey]: value }))
      return
    }
    void saveField(coord.rowId, coord.colKey, value)
  }

  // Viewers edit nothing, and an existing row's key value needs editor+. The
  // API enforces both; this just doesn't offer an edit that would be refused.
  function canEditCell(coord: CellCoord, column: Column): boolean {
    if (!canAnnotate) return false
    return !(column.is_key && coord.rowId !== NEW_ROW_ID && !canImport)
  }

  function startEdit(coord: CellCoord) {
    const column = orderedColumns.find((col) => col.key === coord.colKey)
    if (!column || !canEditCell(coord, column)) return
    setDraft(initialDraft(column, getCellValue(coord)))
    setEditingCell(coord)
  }

  function commitEdit() {
    if (!editingCell) return
    saveCell(editingCell, draft)
    setEditingCell(null)
  }

  function toggleBoolean(coord: CellCoord) {
    saveCell(coord, !getCellValue(coord))
  }

  function moveActive(deltaRow: number, deltaCol: number) {
    if (!activeCell || orderedColumns.length === 0 || displayRows.length === 0) return
    const rowIndex = displayRows.findIndex((row) => row.id === activeCell.rowId)
    const colIndex = orderedColumns.findIndex((col) => col.key === activeCell.colKey)
    if (rowIndex === -1 || colIndex === -1) return
    let newRow = rowIndex
    let newCol = colIndex
    if (deltaCol !== 0) {
      newCol += deltaCol
      if (newCol < 0) {
        newCol = orderedColumns.length - 1
        newRow -= 1
      } else if (newCol >= orderedColumns.length) {
        newCol = 0
        newRow += 1
      }
    }
    newRow = Math.max(0, Math.min(newRow + deltaRow, displayRows.length - 1))
    newCol = Math.max(0, Math.min(newCol, orderedColumns.length - 1))
    setActiveCell({ rowId: displayRows[newRow].id, colKey: orderedColumns[newCol].key })
  }

  function openDrawer(rowId: string, colKey: string) {
    setDrawerFocusKey(colKey)
    setDrawerRowId(rowId)
  }

  function closeDrawer() {
    setDrawerRowId(null)
    setDrawerFocusKey(undefined)
  }

  function handleCellFocus(coord: CellCoord) {
    if (editingCell && (editingCell.rowId !== coord.rowId || editingCell.colKey !== coord.colKey)) {
      setEditingCell(null)
    }
    if (!activeCell || activeCell.rowId !== coord.rowId || activeCell.colKey !== coord.colKey) {
      setActiveCell(coord)
    }
  }

  function handleAddRow() {
    if (pendingData !== null) return
    setPendingData({})
    if (orderedColumns.length > 0) setActiveCell({ rowId: NEW_ROW_ID, colKey: orderedColumns[0].key })
  }

  function applyView(view: SavedView) {
    setFilters(view.filters)
    setMissingRequired(Boolean(view.missingRequired))
    setSearchInput(view.filters.q ?? '')
  }

  function saveCurrentView() {
    if (!datasetId) return
    const name = window.prompt('Name this view')?.trim()
    if (!name) return
    const view: SavedView = { id: crypto.randomUUID(), name, filters: { ...filters }, missingRequired }
    setCustomViews((prev) => {
      const next = [...prev, view]
      saveCustomViews(datasetId, next)
      return next
    })
  }

  function deleteCustomView(id: string) {
    if (!datasetId) return
    setCustomViews((prev) => {
      const next = prev.filter((v) => v.id !== id)
      saveCustomViews(datasetId, next)
      return next
    })
  }

  function handleTableKeyDown(event: KeyboardEvent<HTMLTableElement>) {
    if (!activeCell) return
    const column = orderedColumns.find((col) => col.key === activeCell.colKey)
    if (!column) return

    if (editingCell) {
      if (column.type === 'multi_select') return // MultiSelectEditor handles its own keys
      if (event.key === 'Enter') {
        event.preventDefault()
        commitEdit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        setEditingCell(null)
      } else if (event.key === 'Tab') {
        event.preventDefault()
        commitEdit()
        moveActive(0, event.shiftKey ? -1 : 1)
      }
      return
    }

    switch (event.key) {
      case 'ArrowUp':
        event.preventDefault()
        moveActive(-1, 0)
        break
      case 'ArrowDown':
        event.preventDefault()
        moveActive(1, 0)
        break
      case 'ArrowLeft':
        event.preventDefault()
        moveActive(0, -1)
        break
      case 'ArrowRight':
        event.preventDefault()
        moveActive(0, 1)
        break
      case 'Tab':
        event.preventDefault()
        moveActive(0, event.shiftKey ? -1 : 1)
        break
      case 'Enter':
        event.preventDefault()
        if (opensDrawer(column)) openDrawer(activeCell.rowId, activeCell.colKey)
        else if (column.type === 'boolean') {
          if (canEditCell(activeCell, column)) toggleBoolean(activeCell)
        } else startEdit(activeCell)
        break
      case ' ':
        if (column.type === 'boolean') {
          event.preventDefault()
          if (canEditCell(activeCell, column)) toggleBoolean(activeCell)
        }
        break
      default:
        break
    }
  }

  // Column defs are rebuilt every render (cheap at grid scale) rather than
  // memoized, since the cell renderer closes over editingCell/draft and the
  // edit callbacks, all of which can change on any render.
  const tableColumns = orderedColumns.map((column) =>
    columnHelper.display({
      id: column.key,
      header: () => (
        <>
          {column.is_key && <KeyIcon />}
          {column.label + (column.required ? ' *' : '')}
        </>
      ),
      cell: (ctx): ReactNode => {
        const row = ctx.row.original
        const isEditing = editingCell?.rowId === row.id && editingCell.colKey === column.key
        if (isEditing) {
          if (column.type === 'multi_select') {
            return (
              <MultiSelectEditor
                column={column}
                draft={Array.isArray(draft) ? (draft as string[]) : []}
                onDraftChange={setDraft}
                onCommit={commitEdit}
                onCancel={() => setEditingCell(null)}
                onTab={(shiftKey) => {
                  commitEdit()
                  moveActive(0, shiftKey ? -1 : 1)
                }}
              />
            )
          }
          return <CellEditor column={column} draft={draft} onDraftChange={setDraft} />
        }
        if (column.type === 'boolean') {
          return <input type="checkbox" tabIndex={-1} readOnly checked={Boolean(row.data[column.key])} />
        }
        return <CellDisplay column={column} value={row.data[column.key]} />
      },
    }),
  )
  const table = useTable({ features: gridFeatures, data: displayRows, columns: tableColumns })

  if (loadError) return <p className="error">{loadError}</p>
  if (!columns || !rows) return <p>Loading dataset…</p>

  const drawerRow = drawerRowId ? displayRows.find((row) => row.id === drawerRowId) : undefined
  const isMyQueueView = filtersEqual(filters, DEFAULT_VIEW.filters, missingRequired, DEFAULT_VIEW.missingRequired)

  return (
    <div className="dataset-page">
      <div className="page-toolbar">
        <Link to="/">&larr; Datasets</Link>
        {canImport && (
          <button type="button" onClick={() => setImportOpen(true)}>
            Import
          </button>
        )}
        <button type="button" onClick={() => setVersionsOpen(true)}>
          Versions
        </button>
      </div>
      <FilterBar
        filters={filters}
        missingRequired={missingRequired}
        searchInput={searchInput}
        onSearchInputChange={setSearchInput}
        onFiltersChange={setFilters}
        onMissingRequiredChange={setMissingRequired}
        customViews={customViews}
        onApplyView={applyView}
        onSaveView={saveCurrentView}
        onDeleteView={deleteCustomView}
        isAdmin={isAdmin}
        users={users}
      />
      {isMyQueueView && rows.length === 0 && (
        <p className="hint">
          Your queue is empty.{' '}
          <button type="button" onClick={() => applyView(ALL_ROWS_VIEW)}>
            Show all rows
          </button>
        </p>
      )}
      {notice && (
        <p className="notice">
          {notice}{' '}
          <button type="button" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </p>
      )}
      <table className="grid" onKeyDown={handleTableKeyDown}>
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>
              ))}
              <th>Status</th>
              <th>Assignee</th>
              <th>Comments</th>
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const fullRow = row.original.id === NEW_ROW_ID ? null : (rows ?? []).find((r) => r.id === row.original.id) ?? null
            return (
              <tr key={row.original.id}>
                {row.getAllCells().map((cell) => {
                  const column = columnsByKey.get(cell.column.id)
                  if (!column) return null
                  const coord: CellCoord = { rowId: row.original.id, colKey: column.key }
                  const isActive = activeCell?.rowId === coord.rowId && activeCell.colKey === coord.colKey
                  const cellError = cellErrors[`${coord.rowId}:${coord.colKey}`]
                  return (
                    <td
                      key={cell.id}
                      ref={(el) => {
                        const cacheKey = `${coord.rowId}:${coord.colKey}`
                        if (el) cellRefs.current.set(cacheKey, el)
                        else cellRefs.current.delete(cacheKey)
                      }}
                      tabIndex={isActive ? 0 : -1}
                      className={`grid-cell${isActive ? ' active' : ''}`}
                      onFocus={() => handleCellFocus(coord)}
                      onDoubleClick={() => {
                        if (opensDrawer(column)) openDrawer(coord.rowId, coord.colKey)
                        else if (column.type !== 'boolean') startEdit(coord)
                      }}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      {cellError && (
                        <div className="cell-error" role="alert">
                          {cellError}
                        </div>
                      )}
                    </td>
                  )
                })}
                {fullRow && (
                  <>
                    <td className="grid-cell workflow-cell" onKeyDown={(event) => event.stopPropagation()}>
                      <StatusControl
                        row={fullRow}
                        readOnly={!canAnnotate}
                        onChange={(status) => handleStatusChange(fullRow.id, status)}
                      />
                    </td>
                    <td className="grid-cell workflow-cell" onKeyDown={(event) => event.stopPropagation()}>
                      <AssigneeControl
                        row={fullRow}
                        currentUserId={user?.id ?? ''}
                        users={isAdmin ? users : null}
                        readOnly={!canAnnotate}
                        onChange={(assignee) => handleAssigneeChange(fullRow.id, assignee)}
                      />
                    </td>
                    <td className="grid-cell workflow-cell" onKeyDown={(event) => event.stopPropagation()}>
                      <button type="button" onClick={() => setCommentsRowId(fullRow.id)}>
                        Comments
                      </button>
                    </td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
      {canAnnotate && (
        <button type="button" disabled={pendingData !== null} onClick={handleAddRow}>
          + Add row
        </button>
      )}
      {drawerRow && (
        <DetailDrawer
          columns={drawerColumns}
          data={drawerRow.data}
          focusColKey={drawerFocusKey}
          readOnly={!canAnnotate}
          onFieldSave={(colKey, value) => saveFromDrawer(drawerRow.id, colKey, value)}
          onClose={closeDrawer}
        />
      )}
      {commentsRowId && datasetId && (
        <CommentsPanel
          datasetId={datasetId}
          rowId={commentsRowId}
          currentUserId={user?.id ?? ''}
          users={isAdmin ? users : null}
          onClose={() => setCommentsRowId(null)}
        />
      )}
      {conflict && (
        <ConflictDialog
          columns={orderedColumns}
          attempted={conflict.payload}
          theirs={conflict.theirs}
          onTakeTheirs={handleTakeTheirs}
          onRetryMine={handleRetryMine}
        />
      )}
      {importOpen && datasetId && (
        <ImportWizard
          datasetId={datasetId}
          columns={orderedColumns}
          onClose={() => setImportOpen(false)}
          onImported={reloadRows}
        />
      )}
      {versionsOpen && datasetId && (
        <VersionsPanel datasetId={datasetId} canCut={canImport} onClose={() => setVersionsOpen(false)} />
      )}
    </div>
  )
}
