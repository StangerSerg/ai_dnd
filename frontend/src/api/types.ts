// Contracts generated from the backend OpenAPI schema (src/api/schema.d.ts).
// Regenerate: backend OpenAPI -> frontend/openapi.json -> `npm run gen:api`.
import type { components } from './schema'
import type { RulesState } from './events'

export type Schemas = components['schemas']

// Live API contracts (typed by the generated schema).
export type User = Schemas['MeResponse']
export type RegisterRequest = Schemas['RegisterRequest']
export type LoginRequest = Schemas['LoginRequest']
export type VerifyEmailRequest = Schemas['VerifyEmailRequest']
export type MePatch = Schemas['PatchMeRequest']
export type CsrfToken = Schemas['CsrfResponse']
export type Balance = Schemas['BalanceResponse']

// Расширения контракта «лидер-игрок» описаны вручную: schema.d.ts будет
// перегенерирован, когда бэкенд отдаст новый дамп OpenAPI (см. client.ts).

// POST /api/sessions/ — character_id обязателен при dm_mode="ai": создатель
// садится за свой стол лидером и игроком. recruiting/seats_total задают
// публичность и ёмкость стола прямо при создании.
export type CreateSessionRequest = Schemas['CreateSessionRequest'] & {
  /** Персонаж создателя; обязателен при dm_mode="ai". */
  character_id?: string
  /** Публичный стол (виден в зале); по умолчанию false. */
  recruiting?: boolean
  /** Ёмкость стола 1..12; null = без ограничения. */
  seats_total?: number | null
}

export type CreateSessionResponse = Schemas['CreateSessionResponse'] & {
  recruiting: boolean
  seats_total: number | null
  /** Роль создателя: "player" (ai) | "dm" (human). */
  role: string
}

export type JoinRequest = Schemas['JoinRequest']
export type JoinResponse = Schemas['JoinResponse']
// GET /api/sessions/{id}: is_host — «я лидер этого стола». Лидерство больше
// не следует из роли места "dm" (в ai-режиме лидер сидит игроком).
export type GameSession = Schemas['SessionResponse'] & {
  is_host: boolean
}
export type SeatInfo = Schemas['SeatInfo']
export type GameEvent = Schemas['EventOut']
export type DmMode = CreateSessionRequest['dm_mode']
export type Improvisation = CreateSessionRequest['improvisation']

// UI-only helpers (not part of the server contract).
export type Locale = 'ru' | 'en'
export type UserRole = 'user' | 'moderator' | 'admin'

// FastAPI error body: plain string or a machine code object.
export interface ApiErrorBody {
  detail: string | { code: string }
}

// A real session created/joined from this browser. Used for invite tokens and
// as the fallback list while GET /api/sessions (list) is being built.
export interface KnownSession {
  id: string
  invite_token?: string
  scenario_version_id?: string
  scenario?: string
  language: string
  dm_mode: string
  role: 'dm' | 'player' | 'spectator'
  /** Публичность/ёмкость на момент создания (лидер может поменять их в зале). */
  recruiting?: boolean
  seats_total?: number | null
  created_at: string
}

// POST /api/sessions/{id}/actions (realtime/turns.py ActionResponse).
export interface ActionResponse {
  mode: 'ai' | 'human' | string
  window_id?: string | null
  pending?: number | null
  turn_started: boolean
  seq?: number | null
}

// POST /api/sessions/{id}/turn (realtime/turns.py TurnTriggerResponse).
export interface TurnTriggerResponse {
  window_id: string
  actions: number
}

// game/session.py SessionState as delivered by GET /api/sessions/{id}
// (facts/summary are blanked for players). DEBT: generate with the event schemas.
export interface SessionStateView {
  rules: RulesState
  scene_id: string
  visited: string[]
  revealed: string[]
  attitudes: Record<string, string>
  inventory: Record<string, { item_id: string | null; name: string }[]>
  facts: string[]
  started_encounters: string[]
  summary: string
  summary_through_turn: number
  turn: number
}

export function sessionStateView(session: GameSession): SessionStateView {
  return session.state as unknown as SessionStateView
}