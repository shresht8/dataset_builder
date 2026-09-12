// Typed fetch wrapper for the Groundline API (§8). Every request sends
// credentials so the signed session cookie (GL-1-9) round-trips; the Vite dev
// server proxies /v1 to the backend so this works same-origin in dev.
import type {
  Dataset,
  ImportCommitResponse,
  ImportPreviewResponse,
  Row,
  RowComment,
  RowEdit,
  RowStatus,
  Schema,
  User,
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

export function listDatasets(): Promise<Dataset[]> {
  return apiFetch<Dataset[]>('/datasets')
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

// Import preview (§4, GL-2-9): parses the upload only, persists nothing.
// Without `mapping`, returns detected columns + a suggested auto-mapping and
// a sample. With `mapping`, additionally returns the full validation report.
export function previewImport(
  datasetId: string,
  file: File,
  mapping?: Record<string, string>,
): Promise<ImportPreviewResponse> {
  const form = new FormData()
  form.append('file', file)
  if (mapping) form.append('mapping', JSON.stringify(mapping))
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
): Promise<ImportCommitResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('mapping', JSON.stringify(mapping))
  if (skip.length > 0) form.append('skip', JSON.stringify(skip))
  if (Object.keys(fixes).length > 0) form.append('fixes', JSON.stringify(fixes))
  return apiFetchForm<ImportCommitResponse>(`/datasets/${datasetId}/import`, form)
}
