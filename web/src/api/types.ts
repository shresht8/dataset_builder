// Mirrors backend/groundline_api/schemas + shared/groundline_schema (§3, §5).
// Keep in sync with the Python source of truth; do not diverge field names.

export type Role = 'admin' | 'editor' | 'annotator' | 'viewer'

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
  active: boolean
  created_at: string
}

// The six column types (§3) — deliberately small, single source of truth
// shared by API and CLI (shared/groundline_schema/column_types.py).
export type ColumnType =
  | 'text'
  | 'long_text'
  | 'select'
  | 'multi_select'
  | 'number'
  | 'boolean'

export interface Column {
  key: string
  label: string
  type: ColumnType
  options: string[] | null
  required: boolean
  order: number
  archived: boolean
}

export interface Dataset {
  id: string
  name: string
  description: string | null
  feature_id: string | null
  created_by: string | null
  created_at: string
}

export interface Schema {
  columns: Column[]
}

// Mirrors backend/groundline_api/schemas/row.py RowRead. `status`/`assignee`
// are omitted from the wire today (GL-2-3 not yet landed) but kept optional
// here so this type doesn't need to change when they arrive.
export interface Row {
  id: string
  dataset_id: string
  data: Record<string, unknown>
  rev: number
  status?: string
  assignee?: string | null
  updated_by: string | null
  updated_at: string
  created_at: string
}
