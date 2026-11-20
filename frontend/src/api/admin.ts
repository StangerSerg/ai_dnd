// Admin core endpoints (task #30, wave 1), typed from the generated OpenAPI
// schema (npm run gen:api -> src/api/schema.d.ts). Source of truth for the
// contracts: backend/src/ai_dnd/admin/router.py (#27). All routes live under
// /api/admin and require the "admin" role; mutations carry CSRF via request().
// Wave 2 (supervision #28 / ops #29) extends this module with its own calls.
import { request } from './client'
import type { components } from './schema'

type Schemas = components['schemas']

export type AdminUser = Schemas['AdminUserItem']
export type AdminUsersResponse = Schemas['UsersResponse']
export type AdminRole = Schemas['RoleUpdateRequest']['role']
export type BanRequest = Schemas['BanRequest']
export type ResetSessionsResponse = Schemas['ResetSessionsResponse']
export type AdjustRequest = Schemas['AdjustRequest']
export type AdjustResponse = Schemas['AdjustResponse']
export type LimitItem = Schemas['LimitItem']
export type LimitsResponse = Schemas['LimitsResponse']
export type LimitUpsertRequest = Schemas['LimitUpsertRequest']
export type LimitScope = LimitUpsertRequest['scope']
export type FusesResponse = Schemas['FusesResponse']
export type AuditEntry = Schemas['AuditEntry']
export type AuditResponse = Schemas['AuditResponse']

// Wave 2 (supervision #28 / ops #29). Sessions/ops are admin-only on the
// backend; scenarios/complaints also accept the moderator role.
export type AdminSession = Schemas['SessionItem']
export type AdminSessionsResponse = Schemas['SessionsResponse']
export type AdminSessionDetail = Schemas['SessionDetail']
export type AdminSeatDetail = Schemas['SeatDetail']
export type AdminEventsResponse = Schemas['EventsResponse']
export type AdminEvent = Schemas['EventOut']
export type StopResponse = Schemas['StopResponse']
export type MessageResponse = Schemas['MessageResponse']
export type AdminScenario = Schemas['ScenarioItem']
export type AdminScenariosResponse = Schemas['ScenariosResponse']
export type ScenarioVisibility = Schemas['VisibilityRequest']['visibility']
export type AdminComplaint = Schemas['ComplaintItem']
export type AdminComplaintsResponse = Schemas['ComplaintsResponse']
export type ResolveRequest = Schemas['ResolveRequest']
export type ComplaintStatus = ResolveRequest['status']
export type OpsHealth = Schemas['ai_dnd__admin__ops__HealthResponse']
export type WatcherState = Schemas['WatcherResponse']
export type WatcherCheck = Schemas['WatcherCheck']
export type WatcherAlert = Schemas['WatcherAlert']
export type AckResponse = Schemas['AckResponse']
export type AnalyticsResponse = Schemas['AnalyticsResponse']
export type AnalyticsDay = Schemas['AnalyticsDay']

export interface UsersQuery {
  q?: string
  limit?: number
  offset?: number
}

export interface AuditQuery {
  limit?: number
  offset?: number
  action?: string
  actor_id?: string
}

export interface SessionsQuery {
  status?: 'active' | 'stopped'
  q?: string
  limit?: number
  offset?: number
}

export interface EventsQuery {
  since?: number
  limit?: number
}

export interface ScenariosQuery {
  limit?: number
  offset?: number
}

export interface ComplaintsQuery {
  status?: 'open' | 'resolved' | 'dismissed'
  limit?: number
  offset?: number
}

const encode = encodeURIComponent

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

function usersPath({ q, limit, offset }: UsersQuery): string {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (limit !== undefined) params.set('limit', String(limit))
  if (offset !== undefined) params.set('offset', String(offset))
  const qs = params.toString()
  return `/api/admin/users${qs ? `?${qs}` : ''}`
}

export const admin = {
  listUsers: (query: UsersQuery = {}): Promise<AdminUsersResponse> =>
    request<AdminUsersResponse>(usersPath(query)),

  changeRole: (userId: string, role: AdminRole): Promise<AdminUser> =>
    request<AdminUser>(`/api/admin/users/${encode(userId)}/role`, {
      method: 'POST',
      body: JSON.stringify({ role }),
    }),

  setBan: (userId: string, banned: boolean): Promise<AdminUser> =>
    request<AdminUser>(`/api/admin/users/${encode(userId)}/ban`, {
      method: 'POST',
      body: JSON.stringify({ banned }),
    }),

  resetSessions: (userId: string): Promise<ResetSessionsResponse> =>
    request<ResetSessionsResponse>(`/api/admin/users/${encode(userId)}/reset-sessions`, { method: 'POST' }),

  adjustCredits: (body: AdjustRequest): Promise<AdjustResponse> =>
    request<AdjustResponse>('/api/admin/billing/adjust', { method: 'POST', body: JSON.stringify(body) }),

  getLimits: (): Promise<LimitsResponse> => request<LimitsResponse>('/api/admin/billing/limits'),

  putLimit: (body: LimitUpsertRequest): Promise<LimitItem> =>
    request<LimitItem>('/api/admin/billing/limits', { method: 'PUT', body: JSON.stringify(body) }),

  deleteLimit: (scope: LimitScope, scopeId?: string): Promise<Schemas['StatusResponse']> => {
    const params = new URLSearchParams({ scope })
    if (scopeId) params.set('scope_id', scopeId)
    return request<Schemas['StatusResponse']>(`/api/admin/billing/limits?${params.toString()}`, { method: 'DELETE' })
  },

  getFuses: (): Promise<FusesResponse> => request<FusesResponse>('/api/admin/billing/fuses'),

  listAudit: ({ limit, offset, action, actor_id }: AuditQuery = {}): Promise<AuditResponse> => {
    const params = new URLSearchParams()
    if (limit !== undefined) params.set('limit', String(limit))
    if (offset !== undefined) params.set('offset', String(offset))
    if (action) params.set('action', action)
    if (actor_id) params.set('actor_id', actor_id)
    const qs = params.toString()
    return request<AuditResponse>(`/api/admin/audit${qs ? `?${qs}` : ''}`)
  },

  // --- Wave 2: supervision (backend/src/ai_dnd/admin/supervision.py) ---

  listSessions: ({ status, q, limit, offset }: SessionsQuery = {}): Promise<AdminSessionsResponse> =>
    request<AdminSessionsResponse>(`/api/admin/sessions${queryString({ status, q, limit, offset })}`),

  getSession: (sessionId: string): Promise<AdminSessionDetail> =>
    request<AdminSessionDetail>(`/api/admin/sessions/${encode(sessionId)}`),

  getSessionEvents: (sessionId: string, { since, limit }: EventsQuery = {}): Promise<AdminEventsResponse> =>
    request<AdminEventsResponse>(`/api/admin/sessions/${encode(sessionId)}/events${queryString({ since, limit })}`),

  stopSession: (sessionId: string, stopped: boolean): Promise<StopResponse> =>
    request<StopResponse>(`/api/admin/sessions/${encode(sessionId)}/stop`, {
      method: 'POST',
      body: JSON.stringify({ stopped }),
    }),

  sendSessionMessage: (sessionId: string, text: string): Promise<MessageResponse> =>
    request<MessageResponse>(`/api/admin/sessions/${encode(sessionId)}/message`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  listScenarios: ({ limit, offset }: ScenariosQuery = {}): Promise<AdminScenariosResponse> =>
    request<AdminScenariosResponse>(`/api/admin/scenarios${queryString({ limit, offset })}`),

  setScenarioVisibility: (scenarioId: string, visibility: ScenarioVisibility): Promise<AdminScenario> =>
    request<AdminScenario>(`/api/admin/scenarios/${encode(scenarioId)}/visibility`, {
      method: 'POST',
      body: JSON.stringify({ visibility }),
    }),

  listComplaints: ({ status, limit, offset }: ComplaintsQuery = {}): Promise<AdminComplaintsResponse> =>
    request<AdminComplaintsResponse>(`/api/admin/complaints${queryString({ status, limit, offset })}`),

  resolveComplaint: (complaintId: string, body: ResolveRequest): Promise<AdminComplaint> =>
    request<AdminComplaint>(`/api/admin/complaints/${encode(complaintId)}/resolve`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // --- Wave 2: ops (backend/src/ai_dnd/admin/ops.py, admin-only) ---

  getOpsHealth: (): Promise<OpsHealth> => request<OpsHealth>('/api/admin/ops/health'),

  getWatcher: (): Promise<WatcherState> => request<WatcherState>('/api/admin/ops/watcher'),

  runWatcher: (): Promise<WatcherCheck> => request<WatcherCheck>('/api/admin/ops/watcher/run', { method: 'POST' }),

  ackAlert: (alertId: string): Promise<AckResponse> =>
    request<AckResponse>(`/api/admin/ops/alerts/${encode(alertId)}/ack`, { method: 'POST' }),

  getAnalytics: (days: number): Promise<AnalyticsResponse> =>
    request<AnalyticsResponse>(`/api/admin/ops/analytics${queryString({ days })}`),
}