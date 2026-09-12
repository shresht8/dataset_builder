// Typed fetch wrapper for the Groundline API (§8). Every request sends
// credentials so the signed session cookie (GL-1-9) round-trips; the Vite dev
// server proxies /v1 to the backend so this works same-origin in dev.
import type { Dataset, Row, Schema, User } from './types'

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

export function listRows(datasetId: string): Promise<Row[]> {
  return apiFetch<Row[]>(`/datasets/${datasetId}/rows`)
}

export function createRow(datasetId: string, data: Record<string, unknown>): Promise<Row> {
  return apiFetch<Row>(`/datasets/${datasetId}/rows`, {
    method: 'POST',
    body: JSON.stringify({ data }),
  })
}

// Optimistic-locking update (§4): `rev` is sent as If-Match and the API
// returns 409 with the row's current state on mismatch (surfaced via
// ApiError.body so the caller can refresh without silently overwriting).
export function patchRow(
  datasetId: string,
  rowId: string,
  rev: number,
  data: Record<string, unknown>,
): Promise<Row> {
  return apiFetch<Row>(`/datasets/${datasetId}/rows/${rowId}`, {
    method: 'PATCH',
    headers: { 'If-Match': String(rev) },
    body: JSON.stringify({ data }),
  })
}
