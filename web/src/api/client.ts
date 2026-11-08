// Typed fetch wrapper for the Groundline API (§8). Every request sends
// credentials so the signed session cookie (GL-1-9) round-trips; the Vite dev
// server proxies /v1 to the backend so this works same-origin in dev.
import type {
  ApiToken,
  ApiTokenCreated,
  BundleReport,
  Collection,
  CollectionTree,
  ColumnInput,
  Dataset,
  Folder,
  InferResponse,
  ImportCommitResponse,
  ImportPreviewResponse,
  Row,
  RowComment,
  RowEdit,
  RowStatus,
  Schema,
  StoreEntry,
  User,
  VersionRead,
} from './types'

export class ApiError extends Error {
  status: number
  // Parsed JSON response body, when present. Used for the 409 conflict
  // response on row PATCH, which carries the row's fresh state (§4).
  body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/v1${path}`, {
    credentials: 'include',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? response.statusText, body)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export function login(email: string): Promise<User> {
  return apiFetch<User>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function fetchCurrentUser(): Promise<User> {
  return apiFetch<User>('/auth/me')
}

export function logout(): Promise<void> {
  return apiFetch<void>('/auth/logout', { method: 'POST' })
}

export interface DatasetFilters {
  collectionId?: string
  folderId?: string
  unfiled?: boolean
}

export function listDatasets(filters: DatasetFilters = {}): Promise<Dataset[]> {
  const params = new URLSearchParams()
  if (filters.collectionId) params.set('collection_id', filters.collectionId)
  if (filters.folderId) params.set('folder_id', filters.folderId)
  if (filters.unfiled) params.set('unfiled', 'true')
  const qs = params.toString()
  return apiFetch<Dataset[]>(`/datasets${qs ? `?${qs}` : ''}`)
}

// Create a dataset with its schema in one transaction (Phase 4). In a
// collection give `store_schema_id` or `columns` (resolved against the store).
export interface DatasetCreatePayload {
  name: string
  description?: string | null
  collection_id?: string | null
  folder_id?: string | null
  store_schema_id?: string | null
  columns?: ColumnInput[] | null
}

export function createDataset(payload: DatasetCreatePayload): Promise<Dataset> {
  return apiFetch<Dataset>('/datasets', { method: 'POST', body: JSON.stringify(payload) })
}

// `collectionId: null` makes the dataset unfiled; its data is kept (D9).
export function moveDataset(datasetId: string, collectionId: string | null, folderId: string | null): Promise<Dataset> {
  return apiFetch<Dataset>(`/datasets/${datasetId}/move`, {
    method: 'POST',
    body: JSON.stringify({ collection_id: collectionId, folder_id: folderId }),
  })
}

export function putSchema(datasetId: string, columns: ColumnInput[]): Promise<Schema> {
  return apiFetch<Schema>(`/datasets/${datasetId}/schema`, {
    method: 'PUT',
    body: JSON.stringify({ columns }),
  })
}

export function applyStoreSchema(datasetId: string, storeSchemaId: string): Promise<Schema> {
  return apiFetch<Schema>(`/datasets/${datasetId}/schema/apply`, {
    method: 'POST',
    body: JSON.stringify({ store_schema_id: storeSchemaId }),
  })
}

// Collections and folders (Phase 4, GL-114).
export function listCollections(): Promise<Collection[]> {
  return apiFetch<Collection[]>('/collections')
}

export function getCollection(collectionId: string): Promise<CollectionTree> {
  return apiFetch<CollectionTree>(`/collections/${collectionId}`)
}

export function createCollection(name: string, description: string | null): Promise<Collection> {
  return apiFetch<Collection>('/collections', { method: 'POST', body: JSON.stringify({ name, description }) })
}

export function deleteCollection(collectionId: string): Promise<void> {
  return apiFetch<void>(`/collections/${collectionId}`, { method: 'DELETE' })
}

export function createFolder(collectionId: string, name: string, parentId: string | null): Promise<Folder> {
  return apiFetch<Folder>(`/collections/${collectionId}/folders`, {
    method: 'POST',
    body: JSON.stringify({ name, parent_id: parentId }),
  })
}

export function renameFolder(folderId: string, name: string): Promise<Folder> {
  return apiFetch<Folder>(`/folders/${folderId}`, { method: 'PATCH', body: JSON.stringify({ name }) })
}

export function deleteFolder(folderId: string): Promise<void> {
  return apiFetch<void>(`/folders/${folderId}`, { method: 'DELETE' })
}

// Schema store (Phase 4, GL-115). A used entry is frozen: PUT/DELETE give 409
// with `used_by` in the body.
export function listStore(collectionId: string): Promise<StoreEntry[]> {
  return apiFetch<StoreEntry[]>(`/collections/${collectionId}/schemas`)
}

export function createStoreEntry(collectionId: string, name: string, columns: ColumnInput[]): Promise<StoreEntry> {
  return apiFetch<StoreEntry>(`/collections/${collectionId}/schemas`, {
    method: 'POST',
    body: JSON.stringify({ name, columns }),
  })
}

export function updateStoreEntry(
  collectionId: string,
  entryId: string,
  payload: { name?: string; columns?: ColumnInput[] },
): Promise<StoreEntry> {
  return apiFetch<StoreEntry>(`/collections/${collectionId}/schemas/${entryId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function duplicateStoreEntry(collectionId: string, entryId: string, name: string): Promise<StoreEntry> {
  return apiFetch<StoreEntry>(`/collections/${collectionId}/schemas/${entryId}/duplicate`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function deleteStoreEntry(collectionId: string, entryId: string): Promise<void> {
  return apiFetch<void>(`/collections/${collectionId}/schemas/${entryId}`, { method: 'DELETE' })
}

export function getDataset(datasetId: string): Promise<Dataset> {
  return apiFetch<Dataset>(`/datasets/${datasetId}`)
}

export function getSchema(datasetId: string): Promise<Schema> {
  return apiFetch<Schema>(`/datasets/${datasetId}/schema`)
}

// GET filters (§4, §8) AND together; assignee accepts a user id or 'me'.
export interface RowFilters {
  status?: RowStatus
  assignee?: string
  q?: string
}

export function listRows(datasetId: string, filters: RowFilters = {}): Promise<Row[]> {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.assignee) params.set('assignee', filters.assignee)
  if (filters.q) params.set('q', filters.q)
  const qs = params.toString()
  return apiFetch<Row[]>(`/datasets/${datasetId}/rows${qs ? `?${qs}` : ''}`)
}

export function createRow(datasetId: string, data: Record<string, unknown>): Promise<Row> {
  return apiFetch<Row>(`/datasets/${datasetId}/rows`, {
    method: 'POST',
    body: JSON.stringify({ data }),
  })
}

// Partial update: any of data/status/assignee (§4, §8). `assignee: null`
// unassigns; omit a field to leave it unchanged.
export interface RowPatchPayload {
  data?: Record<string, unknown>
  status?: RowStatus
  assignee?: string | null
}

// Optimistic-locking update (§4): `rev` is sent as If-Match and the API
// returns 409 with the row's current state on mismatch (surfaced via
// ApiError.body so the caller can refresh without silently overwriting).
export function patchRow(
  datasetId: string,
  rowId: string,
  rev: number,
  payload: RowPatchPayload,
): Promise<Row> {
  return apiFetch<Row>(`/datasets/${datasetId}/rows/${rowId}`, {
    method: 'PATCH',
    headers: { 'If-Match': String(rev) },
    body: JSON.stringify(payload),
  })
}

// Soft delete (GL-3.5-18): editor+, optimistic locking as for PATCH (a 409
// carries the row's current state).
export function deleteRow(datasetId: string, rowId: string, rev: number): Promise<void> {
  return apiFetch<void>(`/datasets/${datasetId}/rows/${rowId}`, {
    method: 'DELETE',
    headers: { 'If-Match': String(rev) },
  })
}

// Only a dataset without versions can be deleted (409 otherwise).
export function deleteDataset(datasetId: string): Promise<void> {
  return apiFetch<void>(`/datasets/${datasetId}`, { method: 'DELETE' })
}

export function listComments(datasetId: string, rowId: string): Promise<RowComment[]> {
  return apiFetch<RowComment[]>(`/datasets/${datasetId}/rows/${rowId}/comments`)
}

export function createComment(datasetId: string, rowId: string, body: string): Promise<RowComment> {
  return apiFetch<RowComment>(`/datasets/${datasetId}/rows/${rowId}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  })
}

export function listEdits(datasetId: string, rowId: string): Promise<RowEdit[]> {
  return apiFetch<RowEdit[]>(`/datasets/${datasetId}/rows/${rowId}/edits`)
}

// Admin-only (§5); non-admin callers must handle a 403 (no user-listing
// endpoint exists for lesser roles — see GL-2-7 work log).
export function listUsers(): Promise<User[]> {
  return apiFetch<User[]>('/users')
}

// Like apiFetch, but for the multipart import endpoints: the browser must
// set its own Content-Type (with the multipart boundary), so this can't
// reuse apiFetch's forced 'Content-Type: application/json' header.
async function apiFetchForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`/v1${path}`, {
    method: 'POST',
    credentials: 'include',
    body: form,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? response.statusText, body)
  }
  return response.json() as Promise<T>
}

// Versions (§6, §8, GL-3-2/3/4): cutting is editor+ only, listing is viewer+.
export function listVersions(datasetId: string): Promise<VersionRead[]> {
  return apiFetch<VersionRead[]>(`/datasets/${datasetId}/versions`)
}

export interface VersionCutPayload {
  notes: string | null
  include_unapproved: boolean
}

export function cutVersion(datasetId: string, payload: VersionCutPayload): Promise<VersionRead> {
  return apiFetch<VersionRead>(`/datasets/${datasetId}/versions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// Import preview (§4, GL-2-9): parses the upload only, persists nothing.
// Without `mapping`, returns detected columns + a suggested auto-mapping and
// a sample. With `mapping`, additionally returns the full validation report.
// `recordsKey` picks the list of records in a JSON/YAML file (GL-3.5-5); when
// one is needed, the 422's body carries `records_key_candidates`.
export function previewImport(
  datasetId: string,
  file: File,
  mapping?: Record<string, string>,
  recordsKey?: string,
): Promise<ImportPreviewResponse> {
  const form = new FormData()
  form.append('file', file)
  if (mapping) form.append('mapping', JSON.stringify(mapping))
  if (recordsKey) form.append('records_key', recordsKey)
  return apiFetchForm<ImportPreviewResponse>(`/datasets/${datasetId}/import/preview`, form)
}

// Import commit (§4, GL-2-9): creates rows that pass validation under
// `mapping` (with `fixes` applied first); rows in `skip` are not attempted.
export function commitImport(
  datasetId: string,
  file: File,
  mapping: Record<string, string>,
  skip: number[],
  fixes: Record<number, Record<string, unknown>>,
  recordsKey?: string,
): Promise<ImportCommitResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('mapping', JSON.stringify(mapping))
  if (skip.length > 0) form.append('skip', JSON.stringify(skip))
  if (Object.keys(fixes).length > 0) form.append('fixes', JSON.stringify(fixes))
  if (recordsKey) form.append('records_key', recordsKey)
  return apiFetchForm<ImportCommitResponse>(`/datasets/${datasetId}/import`, form)
}

// Schema inference (GL-117): drafts columns from a data file; creates nothing.
// A 422 may carry `records_key_candidates`, as import preview does.
export function inferSchema(file: File, recordsKey?: string): Promise<InferResponse> {
  const form = new FormData()
  form.append('file', file)
  if (recordsKey) form.append('records_key', recordsKey)
  return apiFetchForm<InferResponse>('/schema/infer', form)
}

// Collection bundles (GL-122). Export returns the zip and the paths of
// datasets skipped by a `latest` export (they have no version).
export async function exportCollection(
  collectionId: string,
  source: 'draft' | 'latest',
): Promise<{ blob: Blob; skipped: string[] }> {
  const response = await fetch(`/v1/collections/${collectionId}/export?source=${source}`, {
    credentials: 'include',
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? response.statusText, body)
  }
  const skipped = JSON.parse(response.headers.get('X-Groundline-Skipped') ?? '[]') as string[]
  return { blob: await response.blob(), skipped }
}

function bundleForm(file: File, name?: string): FormData {
  const form = new FormData()
  form.append('file', file)
  if (name) form.append('name', name)
  return form
}

export function previewBundle(file: File, name?: string): Promise<BundleReport> {
  return apiFetchForm<BundleReport>('/collections/import/preview', bundleForm(file, name))
}

export function importBundle(file: File, name?: string): Promise<Collection> {
  return apiFetchForm<Collection>('/collections/import', bundleForm(file, name))
}

// Personal access tokens (GL-1-10, page GL-3.5-19). A token acts as the user
// who created it; the raw value is only returned by createToken.
export function listTokens(): Promise<ApiToken[]> {
  return apiFetch<ApiToken[]>('/auth/tokens')
}

export function createToken(name: string, ttlDays: number): Promise<ApiTokenCreated> {
  return apiFetch<ApiTokenCreated>('/auth/tokens', {
    method: 'POST',
    body: JSON.stringify({ name, ttl_days: ttlDays }),
  })
}

export function revokeToken(tokenId: string): Promise<void> {
  return apiFetch<void>(`/auth/tokens/${tokenId}`, { method: 'DELETE' })
}
