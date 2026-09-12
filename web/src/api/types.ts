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

// Row status flow (§4): draft -> needs_review -> approved.
export type RowStatus = 'draft' | 'needs_review' | 'approved'

// Mirrors backend/groundline_api/schemas/row.py RowRead.
export interface Row {
  id: string
  dataset_id: string
  data: Record<string, unknown>
  rev: number
  status: RowStatus
  assignee: string | null
  updated_by: string | null
  updated_at: string
  created_at: string
}

// Mirrors backend/groundline_api/schemas/row.py RowEditRead (§3 row_edits).
export interface RowEdit {
  id: string
  row_id: string
  field: string
  old_value: unknown
  new_value: unknown
  user_id: string | null
  at: string
}

// Mirrors backend/groundline_api/schemas/comment.py CommentRead (§4).
export interface RowComment {
  id: string
  row_id: string
  user_id: string | null
  body: string
  created_at: string
}

// Mirrors backend/groundline_api/schemas/import_.py (§4, GL-2-9). Row
// indices are 1-based over data rows only; the header row is not numbered.
export interface ImportSampleRow {
  row: number
  values: Record<string, string>
}

export interface ImportValidationError {
  row: number
  column: string
  reason: string
}

export interface ImportValidationReport {
  valid: number
  errors: ImportValidationError[]
}

export interface ImportPreviewResponse {
  columns: string[]
  suggested_mapping: Record<string, string>
  sample_rows: ImportSampleRow[]
  total_rows: number
  validation: ImportValidationReport | null
}

export interface ImportCommitResponse {
  imported: number
  skipped: number
  errors: ImportValidationError[]
}
