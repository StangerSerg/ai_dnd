import { ApiError, request } from './client'
import type {
  ActionResponse,
  AuthorItemsResponse,
  AuthorScenario,
  Balance,
  CreateSessionRequest,
  CreateSessionResponse,
  CsrfToken,
  GameSession,
  IssueInfo,
  ItemActionRequest,
  ItemActionResponse,
  ItemCatalog,
  ItemIconsResponse,
  ItemUpdateRequest,
  ItemUpdateResponse,
  JoinRequest,
  JoinResponse,
  LoginRequest,
  MePatch,
  RegisterRequest,
  TurnTriggerResponse,
  User,
  VerifyEmailRequest,
} from './types'

// Live API: typed by the generated OpenAPI schema. Session cookie is httpOnly
// (ai_dnd_session); every request carries credentials, mutations carry CSRF.
export const api = {
  register: (body: RegisterRequest) =>
    request<User>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  verifyEmail: (body: VerifyEmailRequest) =>
    request<{ status: string }>('/api/auth/verify-email', { method: 'POST', body: JSON.stringify(body) }),
  login: (body: LoginRequest) =>
    request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),
  getMe: () => request<User>('/api/me'),
  patchMe: (patch: MePatch) =>
    request<User>('/api/me', { method: 'PATCH', body: JSON.stringify(patch) }),
  getBalance: () => request<Balance>('/api/billing/balance'),
  createSession: (body: CreateSessionRequest) =>
    request<CreateSessionResponse>('/api/sessions/', { method: 'POST', body: JSON.stringify(body) }),
  joinSession: (body: JoinRequest) =>
    request<JoinResponse>('/api/sessions/join', { method: 'POST', body: JSON.stringify(body) }),
  getSession: (id: string) => request<GameSession>(`/api/sessions/${encodeURIComponent(id)}`),
  getEvents: (id: string, since: number) =>
    request<{ events: GameSession['events']; last_seq: number }>(
      `/api/sessions/${encodeURIComponent(id)}/events?since=${since}`,
    ),
  submitAction: (id: string, text: string) =>
    request<ActionResponse>(`/api/sessions/${encodeURIComponent(id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  triggerTurn: (id: string) =>
    request<TurnTriggerResponse>(`/api/sessions/${encodeURIComponent(id)}/turn`, { method: 'POST' }),
  // --- Предметы (подэтап 7): каталог для куклы, действия игрока, иконки ---
  getItemCatalog: (id: string) =>
    request<ItemCatalog>(`/api/sessions/${encodeURIComponent(id)}/item-catalog`),
  itemAction: (id: string, body: ItemActionRequest) =>
    request<ItemActionResponse>(`/api/sessions/${encodeURIComponent(id)}/item-action`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getItemIcons: () => request<ItemIconsResponse>('/api/item-icons'),
}

// --- REST автора сценария (подэтап 7): доступ у любого авторизованного,
// сервер сам отдаёт только свои сценарии (403 not_scenario_author иначе). ---

/**
 * 422 invalid_item/invalid_scenario несёт detail.issues — client.request
 * сохраняет только code, поэтому PUT предмета идёт отдельным fetch с тем же
 * CSRF-протоколом, чтобы показать автору список issues.
 */
export class AuthorItemValidationError extends ApiError {
  readonly issues: IssueInfo[]
  constructor(status: number, code: string, issues: IssueInfo[]) {
    super(status, code, code)
    this.name = 'AuthorItemValidationError'
    this.issues = issues
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

async function putAuthorItem(scenarioId: string, itemId: string, body: ItemUpdateRequest): Promise<ItemUpdateResponse> {
  const path = `/api/author/scenarios/${encodeURIComponent(scenarioId)}/items/${encodeURIComponent(itemId)}`
  const send = (token: string) => fetch(`${apiBaseUrl}${path}`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
    body: JSON.stringify(body),
  })
  let response = await send((await fetchCsrf()).token)
  if (response.status === 403) {
    const peek = await response.clone().json().catch(() => null) as { detail?: { code?: string } } | null
    if (peek?.detail?.code === 'csrf_failed') response = await send((await fetchCsrf()).token)
  }
  if (!response.ok) {
    const bodyJson = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = bodyJson?.detail
    if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') {
      const issues = 'issues' in detail && Array.isArray(detail.issues) ? detail.issues as IssueInfo[] : []
      throw new AuthorItemValidationError(response.status, detail.code, issues)
    }
    throw new ApiError(response.status, typeof detail === 'string' ? detail : response.statusText)
  }
  return response.json() as Promise<ItemUpdateResponse>
}

export const author = {
  listScenarios: () => request<AuthorScenario[]>('/api/author/scenarios'),
  getItems: (scenarioId: string) =>
    request<AuthorItemsResponse>(`/api/author/scenarios/${encodeURIComponent(scenarioId)}/items`),
  updateItem: putAuthorItem,
}

export async function fetchCsrf(): Promise<CsrfToken> {
  return request<CsrfToken>('/api/auth/csrf')
}

export { ApiError, apiErrorMessage } from './client'
export { admin } from './admin'
export type {
  AdjustRequest,
  AdjustResponse,
  AdminRole,
  AdminUser,
  AdminUsersResponse,
  AuditEntry,
  AuditQuery,
  AuditResponse,
  BanRequest,
  FusesResponse,
  LimitItem,
  LimitScope,
  LimitsResponse,
  LimitUpsertRequest,
  ResetSessionsResponse,
  UsersQuery,
} from './admin'
export { portal, localized } from './portal'
export type {
  CharacterSheet,
  CharacterSummary,
  CombatOption,
  CombatOptions,
  CreateCharacterRequest,
  ScenarioSummary,
  SessionPreview,
  SessionSummary,
  SheetField,
  SheetSection,
  SheetTemplate,
  UpdateCharacterRequest,
} from './portal'
export * from './types'