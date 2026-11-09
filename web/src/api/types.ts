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

// The column types (§3) — deliberately small, single source of truth
// shared by API and CLI (shared/groundline_schema/column_types.py).
export type ColumnType =
  | 'text'
  | 'long_text'
  | 'select'
  | 'multi_select'
  | 'number'
  | 'boolean'
  | 'json'
  | 'date'

export interface Column {
  key: string
  label: string
  type: ColumnType
  options: string[] | null
  required: boolean
  order: number
  archived: boolean
  // `json` columns only: an optional JSON Schema their values must match.
  json_schema: Record<string, unknown> | null
  // The one text column whose value identifies a row (GL-3.5-12).
  is_key: boolean
}

// The store entry a collection dataset uses; `created` only on the write that
// added it to the store (Phase 4, D7).
export interface StoreSchemaRef {
  id: string
  name: string
  created: boolean
}

export interface Dataset {
  id: string
  name: string
  description: string | null
  feature_id: string | null
  created_by: string | null
  created_at: string
  // Phase 4: null collection_id = an unfiled dataset.
  collection_id: string | null
  folder_id: string | null
  store_schema_id: string | null
  // `collection/folder/.../name`, or the bare name when unfiled.
  path: string
  store_schema: StoreSchemaRef | null
}

export interface Schema {
  columns: Column[]
  store_schema?: StoreSchemaRef | null
}

// A column as the schema endpoints accept it (order/archived are server-managed).
export interface ColumnInput {
  key: string
  label: string
  type: ColumnType
  options?: string[] | null
  required: boolean
  json_schema?: Record<string, unknown> | null
  is_key?: boolean
}

// Mirrors backend/groundline_api/schemas/collection.py (Phase 4, C5).
export interface Collection {
  id: string
  name: string
  description: string | null
  created_by: string | null
  created_at: string
  dataset_count: number
}

export interface Folder {
  id: string
  collection_id: string
  name: string
  parent_id: string | null
}

export interface TreeDataset {
  id: string
  name: string
  description: string | null
  folder_id: string | null
  store_schema_id: string | null
  store_schema_name: string | null
  row_count: number
  version_count: number
}

export interface CollectionTree extends Collection {
  folders: Folder[]
  datasets: TreeDataset[]
}

export interface UsedBy {
  id: string
  name: string
  path: string
}

export interface StoreEntry {
  id: string
  collection_id: string
  name: string
  columns: Column[]
  content_hash: string
  created_at: string
  updated_at: string
  used_by: UsedBy[]
}

// POST /v1/schema/infer (GL-117).
export interface InferResponse {
  columns: ColumnInput[]
  warnings: string[]
  record_count: number
  delimiter: string | null // CSV only: the one used
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

// Mirrors backend/groundline_api/schemas/import_.py (§4, GL-2-9, GL-3.5-5).
// Row indices are 1-based over data rows only; the header row is not
// numbered. CSV/XLSX sample values are strings; JSON/YAML ones are typed.
export interface ImportSampleRow {
  row: number
  values: Record<string, unknown>
}

export interface ImportValidationError {
  row: number
  column: string
  reason: string
}

export interface ImportColumnProblem {
  column: string // "" = the record as a whole
  rows: number
  reason: string
}

export interface ImportValidationReport {
  valid: number
  // At most 200 rows' worth; the fields below are exact totals (GL-133).
  errors: ImportValidationError[]
  error_total: number
  failed_rows: number
  checked_rows: number // total rows minus blank lines
  by_column: ImportColumnProblem[]
  // Failed rows whose only problem is the key (GL-142): already in the dataset,
  // or repeating a key from an earlier row of the file.
  existing_key_rows: number
  repeated_key_rows: number
  // Warnings only (GL-146): the import still goes ahead.
  // CSV/XLSX rows with cells past the header; those cells aren't imported.
  extra_cells: { rows: number; examples: number[] } | null
  // No key column, and the mapped source `id` repeats or is empty.
  id_check: { column: string; repeated: string[]; repeated_rows: number; missing_rows: number } | null
}

export interface ImportCompatibility {
  matched: string[]
  ignored_source_columns: string[]
  unfilled_schema_columns: string[]
  required_without_source: string[]
}

export interface ImportPreviewResponse {
  columns: string[]
  suggested_mapping: Record<string, string>
  sample_rows: ImportSampleRow[]
  total_rows: number // includes blank CSV/XLSX lines
  blank_skipped: number
  compatibility: ImportCompatibility
  validation: ImportValidationReport | null
  // JSON/YAML only: the key the records were read from, and the other
  // top-level keys that were ignored.
  records_key: string | null
  ignored_keys: string[]
  // CSV only: the delimiter used (detected unless one was given, GL-141).
  delimiter: string | null
}

export interface ImportCommitResponse {
  imported: number
  skipped: number
  errors: ImportValidationError[]
  blank_skipped: number
}

// Mirrors backend/groundline_api/schemas/version.py (§6, §8, GL-3-2/3).
export interface VersionRead {
  id: string
  dataset_id: string
  path: string
  version: number
  content_hash: string // "sha256:<64 hex>"
  row_count: number
  notes: string | null
  created_by: string | null
  created_by_email: string | null
  created_at: string
}

// Mirrors backend/groundline_api/api/v1/auth.py TokenRead / TokenCreated
// (GL-1-10). The raw token is only ever returned once, on creation.
export interface ApiToken {
  id: string
  name: string
  expires_at: string
  revoked_at: string | null
}

export interface ApiTokenCreated extends ApiToken {
  token: string
}

// POST /v1/collections/import/preview (GL-122).
export interface BundleReport {
  name: string
  name_taken: boolean
  description: string | null
  source: string
  folders: string[]
  store: { name: string; columns: number; error: string | null }[]
  datasets: {
    path: string
    store_schema: string
    description: string | null
    rows: number
    valid: number
    error_count: number
    errors: ImportValidationError[]
  }[]
  errors: string[]
  ok: boolean
}
