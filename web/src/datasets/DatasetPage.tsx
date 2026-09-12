// The typed annotation grid (design §4, GL-2-6): one row per case, columns
// from the dataset schema, keyboard-first navigation and editing, a detail
// drawer for long_text. Row status/assignee/comments/conflict UI/filters are
// out of scope here (GL-2-7).
import { createColumnHelper, flexRender, tableFeatures, useTable } from '@tanstack/react-table'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, createRow, getSchema, listRows, patchRow } from '../api/client'
import type { Column, Row } from '../api/types'
import { CellDisplay, CellEditor, MultiSelectEditor } from './Cell'
import { DetailDrawer } from './DetailDrawer'

interface CellCoord {
  rowId: string
  colKey: string
}

interface DisplayRow {
  id: string
  data: Record<string, unknown>
}

const NEW_ROW_ID = 'new'

// The grid uses no sorting/filtering/selection — just the default (core) row
// model — so the feature set is empty (§4: keyboard nav/editing is all custom).
const gridFeatures = tableFeatures({})
const columnHelper = createColumnHelper<typeof gridFeatures, DisplayRow>()

function isEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string' && value.trim() === '') return true
  if (Array.isArray(value) && value.length === 0) return true
  return false
}

// Mirrors the backend's required check (rows.py:_validate_row_data) so the
// pending new row is only POSTed once it would actually pass validation.
function canCreate(columns: Column[], data: Record<string, unknown>): boolean {
  return columns.every((col) => col.archived || !col.required || !isEmptyValue(data[col.key]))
}

function initialDraft(column: Column, value: unknown): unknown {
  if (column.type === 'number') return typeof value === 'number' ? value : null
  if (column.type === 'multi_select') return Array.isArray(value) ? value : []
  if (column.type === 'boolean') return Boolean(value)
  return typeof value === 'string' ? value : ''
}

export function DatasetPage() {
  const { datasetId } = useParams<{ datasetId: string }>()
  const [columns, setColumns] = useState<Column[] | null>(null)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pendingData, setPendingData] = useState<Record<string, unknown> | null>(null)
  const [activeCell, setActiveCell] = useState<CellCoord | null>(null)
  const [editingCell, setEditingCell] = useState<CellCoord | null>(null)
  const [draft, setDraft] = useState<unknown>(null)
  const [drawerRowId, setDrawerRowId] = useState<string | null>(null)
  const [drawerFocusKey, setDrawerFocusKey] = useState<string | undefined>(undefined)
  const cellRefs = useRef(new Map<string, HTMLTableCellElement>())

  useEffect(() => {
    if (!datasetId) return
    Promise.all([getSchema(datasetId), listRows(datasetId)])
      .then(([schema, fetchedRows]) => {
        setColumns(schema.columns)
        setRows(fetchedRows)
        if (schema.columns.length > 0 && fetchedRows.length > 0) {
          setActiveCell({ rowId: fetchedRows[0].id, colKey: schema.columns[0].key })
        }
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load dataset'))
  }, [datasetId])

  const orderedColumns = useMemo(
    () => (columns ?? []).filter((col) => !col.archived).sort((a, b) => a.order - b.order),
    [columns],
  )
  const longTextColumns = useMemo(
    () => orderedColumns.filter((col) => col.type === 'long_text'),
    [orderedColumns],
  )
  const columnsByKey = useMemo(
    () => new Map(orderedColumns.map((col) => [col.key, col])),
    [orderedColumns],
  )
  const displayRows: DisplayRow[] = useMemo(() => {
    const base: DisplayRow[] = rows ?? []
    return pendingData !== null ? [...base, { id: NEW_ROW_ID, data: pendingData }] : base
  }, [rows, pendingData])

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
        setNotice(err instanceof ApiError ? err.message : 'Could not create row')
      })
    return () => {
      cancelled = true
    }
  }, [datasetId, pendingData, columns, orderedColumns])

  // Roving tabindex: move real DOM focus to the active cell whenever nothing
  // else (an inline editor or the drawer) currently owns focus.
  useEffect(() => {
    if (editingCell || drawerRowId || !activeCell) return
    cellRefs.current.get(`${activeCell.rowId}:${activeCell.colKey}`)?.focus()
  }, [activeCell, editingCell, drawerRowId])

  function getCellValue(coord: CellCoord): unknown {
    return displayRows.find((row) => row.id === coord.rowId)?.data[coord.colKey]
  }

  async function saveField(rowId: string, colKey: string, value: unknown) {
    if (!datasetId) return
    const row = (rows ?? []).find((r) => r.id === rowId)
    if (!row) return
    try {
      const updated = await patchRow(datasetId, rowId, row.rev, { [colKey]: value })
      setRows((prev) => (prev ?? []).map((r) => (r.id === rowId ? updated : r)))
      setNotice(null)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.body) {
        const fresh = err.body as Row
        setRows((prev) => (prev ?? []).map((r) => (r.id === rowId ? fresh : r)))
        setNotice('This row changed since you loaded it — showing the latest version. Your edit was not saved.')
      } else {
        setNotice(err instanceof ApiError ? err.message : 'Save failed')
      }
    }
  }

  function saveCell(coord: CellCoord, value: unknown) {
    if (coord.rowId === NEW_ROW_ID) {
      setPendingData((prev) => ({ ...(prev ?? {}), [coord.colKey]: value }))
      return
    }
    void saveField(coord.rowId, coord.colKey, value)
  }

  function startEdit(coord: CellCoord) {
    const column = orderedColumns.find((col) => col.key === coord.colKey)
    if (!column) return
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
        if (column.type === 'long_text') openDrawer(activeCell.rowId, activeCell.colKey)
        else if (column.type === 'boolean') toggleBoolean(activeCell)
        else startEdit(activeCell)
        break
      case ' ':
        if (column.type === 'boolean') {
          event.preventDefault()
          toggleBoolean(activeCell)
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
      header: column.label + (column.required ? ' *' : ''),
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

  return (
    <div className="dataset-page">
      <p>
        <Link to="/">&larr; Datasets</Link>
      </p>
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
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.original.id}>
              {row.getAllCells().map((cell) => {
                const column = columnsByKey.get(cell.column.id)
                if (!column) return null
                const coord: CellCoord = { rowId: row.original.id, colKey: column.key }
                const isActive = activeCell?.rowId === coord.rowId && activeCell.colKey === coord.colKey
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
                      if (column.type === 'long_text') openDrawer(coord.rowId, coord.colKey)
                      else if (column.type !== 'boolean') startEdit(coord)
                    }}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" disabled={pendingData !== null} onClick={handleAddRow}>
        + Add row
      </button>
      {drawerRow && (
        <DetailDrawer
          columns={longTextColumns}
          data={drawerRow.data}
          focusColKey={drawerFocusKey}
          onFieldSave={(colKey, value) => saveCell({ rowId: drawerRow.id, colKey }, value)}
          onClose={closeDrawer}
        />
      )}
    </div>
  )
}
