import { request } from './client'
import type {
  ActionResponse,
  Balance,
  CreateSessionRequest,
  CreateSessionResponse,
  CsrfToken,
  GameSession,
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