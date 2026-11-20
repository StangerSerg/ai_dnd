// Read/combat portal endpoints, typed from the generated OpenAPI schema
// (npm run gen:api -> src/api/schema.d.ts):
//   GET  /api/sessions                      -> SessionListItem[]
//   GET  /api/sessions/by-invite/{token}    -> SessionPreviewResponse (public)
//   GET  /api/scenarios                     -> ScenarioListItem[]
//   GET  /api/characters                    -> CharacterListItem[]
//   POST /api/characters                    -> 201 CharacterListItem
//   GET  /api/characters/{id}               -> CharacterSheetResponse
//   PATCH /api/characters/{id}              -> CharacterListItem (avatar)
//   GET  /api/sessions/{id}/combat-options  -> CombatOptionsResponse
//   POST /api/sessions/{id}/combat-actions  -> 202 CombatActionResponse
import { request } from './client'
import type { components } from './schema'
import type { JoinResponse } from './types'

type Schemas = components['schemas']

export type SessionSummary = Schemas['SessionListItem']
export type SessionPreview = Schemas['SessionPreviewResponse']
export type ScenarioSummary = Schemas['ScenarioListItem']
export type CharacterSummary = Schemas['CharacterListItem']
export type CreateCharacterRequest = Schemas['CreateCharacterRequest']
export type UpdateCharacterRequest = Schemas['UpdateCharacterRequest']
export type CombatOption = Schemas['CombatOptionOut']
export type CombatOptions = Schemas['CombatOptionsResponse']
export type CombatActionResponse = Schemas['CombatActionResponse']
export type ChatMessage = Schemas['ChatMessageOut']
export type ChatHistory = Schemas['ChatHistoryResponse']
export type TransferHostRequest = Schemas['TransferHostRequest']
export type TransferHostResponse = Schemas['TransferHostResponse']
export type StopSessionResponse = Schemas['StopSessionResponse']
export type RestartSessionResponse = Schemas['RestartSessionResponse']

// --- Sheet template (content/sheets/*.json, sheets/schemas.py) ---
// OpenAPI types CharacterSheetResponse.template as an untyped dict, so the
// field/layout structure stays hand-typed here until the backend exposes a
// SheetTemplate schema.

export type LocalizedLabel = string | Record<string, string>

export interface SheetFieldOption {
  value: string
  label: LocalizedLabel
}

export interface SheetField {
  id: string
  type: 'text' | 'longtext' | 'number' | 'checkbox' | 'select' | 'computed' | 'resource' | 'list' | 'image'
  label: LocalizedLabel
  default?: unknown
  min?: number
  max?: number
  formula?: string
  max_formula?: string
  options?: SheetFieldOption[]
  item?: Record<string, SheetField>
}

export interface SheetSection {
  section: LocalizedLabel
  cols: number
  items: string[]
}

export interface SheetTemplate {
  id: string
  version: number
  name: LocalizedLabel
  ruleset: string
  fields: SheetField[]
  layout: SheetSection[]
  bindings?: Record<string, unknown>
}

export type CharacterSheet = Omit<Schemas['CharacterSheetResponse'], 'template'> & {
  template: SheetTemplate
}

export function localized(label: LocalizedLabel | undefined, language: string): string {
  if (label === undefined) return ''
  if (typeof label === 'string') return label
  return label[language] ?? label.ru ?? label.en ?? Object.values(label)[0] ?? ''
}

const encode = encodeURIComponent

export const portal = {
  listSessions: (): Promise<SessionSummary[]> => request<SessionSummary[]>('/api/sessions'),

  // Public table preview by invite token (capability, no auth required).
  sessionPreview: (inviteToken: string): Promise<SessionPreview> =>
    request<SessionPreview>(`/api/sessions/by-invite/${encode(inviteToken)}`),

  listScenarios: (): Promise<ScenarioSummary[]> => request<ScenarioSummary[]>('/api/scenarios'),

  listCharacters: (): Promise<CharacterSummary[]> => request<CharacterSummary[]>('/api/characters'),

  createCharacter: (body: CreateCharacterRequest): Promise<CharacterSummary> =>
    request<CharacterSummary>('/api/characters', { method: 'POST', body: JSON.stringify(body) }),

  getCharacter: (id: string): Promise<CharacterSheet> => request<CharacterSheet>(`/api/characters/${encode(id)}`),

  // Смена аватара (owner-only): portrait — пресет из game/avatars.ts либо null.
  updateCharacter: (id: string, body: UpdateCharacterRequest): Promise<CharacterSummary> =>
    request<CharacterSummary>(`/api/characters/${encode(id)}`, { method: 'PATCH', body: JSON.stringify(body) }),

  combatOptions: (sessionId: string): Promise<CombatOptions> =>
    request<CombatOptions>(`/api/sessions/${encode(sessionId)}/combat-options`),

  combatAction: (sessionId: string, optionIndex: number): Promise<CombatActionResponse> =>
    request<CombatActionResponse>(`/api/sessions/${encode(sessionId)}/combat-actions`, {
      method: 'POST',
      body: JSON.stringify({ option_index: optionIndex }),
    }),

  // Чат вне игры (#37): история страницами (before — старше сообщения),
  // отправка не блокируется мутом/нокаутом.
  chatHistory: (sessionId: string, options?: { limit?: number; before?: string }): Promise<ChatHistory> => {
    const params = new URLSearchParams()
    params.set('limit', String(options?.limit ?? 50))
    if (options?.before) params.set('before', options.before)
    return request<ChatHistory>(`/api/sessions/${encode(sessionId)}/chat?${params.toString()}`)
  },

  postChat: (sessionId: string, text: string): Promise<ChatMessage> =>
    request<ChatMessage>(`/api/sessions/${encode(sessionId)}/chat`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  // Выход ведущего из-за стола: передача ведения игроку (место игрока
  // удаляется без бана, dm-место переходит к нему) либо остановка стола,
  // если передавать некому. Оба эндпоинта host-only.
  transferHost: (sessionId: string, seatId: string): Promise<TransferHostResponse> =>
    request<TransferHostResponse>(`/api/sessions/${encode(sessionId)}/transfer-host`, {
      method: 'POST',
      body: JSON.stringify({ seat_id: seatId } satisfies TransferHostRequest),
    }),

  stopSession: (sessionId: string): Promise<StopSessionResponse> =>
    request<StopSessionResponse>(`/api/sessions/${encode(sessionId)}/stop`, { method: 'POST' }),

  // TPK-рестарт (host-only): журнал/состояние сброшены, opening придёт заново;
  // комната получает control session_restarted и перечитывает снапшот с нуля.
  restartSession: (sessionId: string): Promise<RestartSessionResponse> =>
    request<RestartSessionResponse>(`/api/sessions/${encode(sessionId)}/restart`, { method: 'POST' }),

  // Наблюдатель (3.8): read-only место без персонажа; ответ как у join.
  observeSession: (sessionId: string): Promise<JoinResponse> =>
    request<JoinResponse>(`/api/sessions/${encode(sessionId)}/observe`, { method: 'POST' }),

  leaveObserve: (sessionId: string): Promise<void> =>
    request<void>(`/api/sessions/${encode(sessionId)}/observe`, { method: 'DELETE' }),
}