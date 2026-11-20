import type { ApiErrorBody, CsrfToken } from './types'
import type { components } from './schema'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** Machine code when the backend sends {"detail": {"code": ...}}. */
  readonly code: string | null

  constructor(status: number, detail: string, code: string | null = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
  }
}

// Same-origin by default: Vite proxies /api and /ws to the backend.
const baseUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

let csrfToken: string | null = null

async function errorFrom(response: Response): Promise<ApiError> {
  let detail = response.statusText
  let code: string | null = null
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>
    if (typeof body.detail === 'string') {
      detail = body.detail
    } else if (body.detail && typeof body.detail.code === 'string') {
      code = body.detail.code
      detail = code
    }
  } catch {
    // non-JSON error body: keep statusText
  }
  return new ApiError(response.status, detail, code)
}

async function fetchCsrfToken(): Promise<string> {
  const response = await fetch(`${baseUrl}/api/auth/csrf`, { credentials: 'include' })
  if (!response.ok) throw await errorFrom(response)
  const body = (await response.json()) as CsrfToken
  csrfToken = body.token
  return body.token
}

async function send(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  if (token) headers.set('X-CSRF-Token', token)
  return fetch(`${baseUrl}${path}`, { ...init, headers, credentials: 'include' })
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const mutating = method !== 'GET' && method !== 'HEAD'
  // CSRF double-submit: GET /api/auth/csrf sets cookie ai_dnd_csrf and returns
  // the token; every mutation repeats it in X-CSRF-Token.
  const token = mutating ? (csrfToken ?? (await fetchCsrfToken())) : null
  let response = await send(path, init, token)
  if (mutating && response.status === 403) {
    const error = await errorFrom(response)
    if (error.code !== 'csrf_failed') throw error
    // Stale token (e.g. cookie rotated): refresh once and retry.
    response = await send(path, init, await fetchCsrfToken())
  }
  if (!response.ok) throw await errorFrom(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function apiErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.detail : String(error)
}

// --- Зал историй (задача #32, часть B) ---
// Типы берутся из сгенерированной OpenAPI-схемы (schema.d.ts не перегенерируем).
type Schemas = components['schemas']
export type OpenTableItem = Schemas['OpenTableItem']
export type RecruitingRequest = Schemas['RecruitingRequest']
export type RecruitingResponse = Schemas['RecruitingResponse']

/** Открытые столы зала: active + recruiting со свободными местами (auth). */
export function openTables(): Promise<OpenTableItem[]> {
  return request<OpenTableItem[]>('/api/sessions/open-tables')
}

/** Лидер команды открывает/закрывает набор и задаёт ёмкость стола (auth, host). */
export function setRecruiting(id: string, body: RecruitingRequest): Promise<RecruitingResponse> {
  return request<RecruitingResponse>(`/api/sessions/${encodeURIComponent(id)}/recruiting`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}