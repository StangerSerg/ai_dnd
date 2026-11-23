// Journal event contracts: hand-written mirrors of the backend pydantic models
//   backend/src/ai_dnd/rules/events.py  (rules engine events)
//   backend/src/ai_dnd/game/events.py   (story events)
// DEBT(3.2): autogenerate these from the backend schemas (like schema.d.ts from
// OpenAPI) instead of maintaining the mirror by hand.

export type Visibility = string // 'public' | 'dm' | 'player:<actor_id>'
export type RollMode = 'normal' | 'advantage' | 'disadvantage'
export type RollReason = 'check' | 'save' | 'contest' | 'attack' | 'damage' | 'healing' | 'initiative'
export type Outcome = 'critical_failure' | 'failure' | 'partial_success' | 'success' | 'critical_success'
export type Difficulty = 'very_easy' | 'easy' | 'medium' | 'hard' | 'very_hard' | 'nearly_impossible'
export type Attitude = 'friendly' | 'neutral' | 'hostile'
export type RevealedKind = 'clue' | 'handout' | 'item' | 'lore'
export type TurnSlot = 'action' | 'bonus_action' | 'movement'
export type ItemSlot = 'weapon' | 'armor' | 'amulet' | 'ring' | 'cloak' | 'other'
export type ItemUseKind = 'heal' | 'buff' | 'spell'

// --- rules/dice.py: RollResult ---

export interface DieRoll {
  value: number
  kept: boolean
}

export interface DiceTermResult {
  kind: 'dice'
  sign: 1 | -1
  notation: string
  sides: number
  rolls: DieRoll[]
  subtotal: number
}

export interface ConstantTermResult {
  kind: 'constant'
  sign: 1 | -1
  value: number
  label?: string | null
  subtotal: number
}

export type TermResult = DiceTermResult | ConstantTermResult

export interface RollResult {
  notation: string
  mode: RollMode
  terms: TermResult[]
  total: number
}

/** «Чистое» значение первой группы костей (20 на d20), без модификаторов. */
export function naturalRoll(roll: RollResult): number | null {
  const term = roll.terms.find((item): item is DiceTermResult => item.kind === 'dice')
  if (!term) return null
  const kept = term.rolls.filter(die => die.kept)
  const first = kept[0] ?? term.rolls[0]
  return first ? first.value : null
}

// --- rules/state.py: Actor (JSON shape used by events and session state) ---

export interface ResourceState {
  current: number
  max: number
}

export interface ActiveCondition {
  id: string
  rounds?: number | null
}

export interface AttackProfile {
  name: string
  bonus: number
  damage?: string | null
  damage_type?: string | null
}

export interface ActorState {
  id: string
  name: string
  kind: 'character' | 'creature'
  checks: Record<string, number>
  saves: Record<string, number>
  ac?: number | null
  initiative: number
  resources: Record<string, ResourceState>
  attacks: Record<string, AttackProfile>
  /** Id скиллов класса ("protect"/"stealth"/"cure_wounds"); опционально для старых снапшотов. */
  skills?: string[]
  /** Кто прикрывает актёра (ProtectAction): атаки по нему принимает защитник. */
  protected_by?: string | null
  resistances: string[]
  vulnerabilities: string[]
  immunities: string[]
  conditions: ActiveCondition[]
  dead: boolean
}

export interface TurnBudget {
  action: number
  bonus_action: number
  movement: number
}

export interface CombatState {
  round: number
  order: string[]
  sides: Record<string, string>
  initiatives: Record<string, number>
  current: string
  remaining: TurnBudget
  fled: string[]
}

export interface RulesState {
  actors: Record<string, ActorState>
  combat: CombatState | null
}

// --- rules/events.py ---

interface EventBase {
  visibility: Visibility
}

export interface DiceRolled extends EventBase {
  type: 'DiceRolled'
  actor_id: string | null
  reason: RollReason
  roll: RollResult
}

export interface CheckResolved extends EventBase {
  type: 'CheckResolved'
  actor_id: string
  kind: 'check' | 'save'
  check: string
  outcome: Outcome
  total: number | null
  natural: number | null
  target: number | null
  difficulty: Difficulty | null
  mode: RollMode
  auto_failed_by?: string | null
}

export interface ContestResolved extends EventBase {
  type: 'ContestResolved'
  actor_id: string
  check: string
  total: number | null
  opponent_id: string
  opponent_check: string
  opponent_total: number | null
  winner_id: string | null
}

export interface AttackResolved extends EventBase {
  type: 'AttackResolved'
  attacker_id: string
  target_id: string
  attack: string
  attack_name: string
  outcome: Outcome
  total: number
  natural: number | null
  target_ac: number
  mode: RollMode
  /** Цель прикрыта: бросок шёл по КД защитника, урон при попадании получает он. */
  redirected_to_id?: string | null
}

export interface DamageDealt extends EventBase {
  type: 'DamageDealt'
  target_id: string
  source_id: string | null
  amount: number
  rolled: number
  damage_type: string | null
  modifiers: ('resistance' | 'vulnerability' | 'immunity')[]
  hp_after: number
}

export interface Healed extends EventBase {
  type: 'Healed'
  target_id: string
  source_id: string | null
  amount: number
  rolled: number
  hp_after: number
}

export interface ActorDied extends EventBase {
  type: 'ActorDied'
  actor_id: string
}

export interface ResourceChanged extends EventBase {
  type: 'ResourceChanged'
  actor_id: string
  resource: string
  delta: number
  current: number
}

export interface ConditionApplied extends EventBase {
  type: 'ConditionApplied'
  actor_id: string
  condition: string
  rounds: number | null
}

export interface ConditionRemoved extends EventBase {
  type: 'ConditionRemoved'
  actor_id: string
  condition: string
  reason: 'removed' | 'expired' | 'healed' | 'attacked'
}

export interface TimePassed extends EventBase {
  type: 'TimePassed'
  rounds: number
}

export interface Rested extends EventBase {
  type: 'Rested'
  rest: 'short' | 'long'
  actor_ids: string[]
}

export interface CombatStarted extends EventBase {
  type: 'CombatStarted'
  sides: Record<string, string>
  order: string[]
  initiatives: Record<string, number>
}

export interface RoundStarted extends EventBase {
  type: 'RoundStarted'
  round: number
}

export interface TurnStarted extends EventBase {
  type: 'TurnStarted'
  actor_id: string
  round: number
  budget: TurnBudget
}

export interface TurnSlotUsed extends EventBase {
  type: 'TurnSlotUsed'
  actor_id: string
  slot: TurnSlot
  label?: string | null
}

export interface TurnEnded extends EventBase {
  type: 'TurnEnded'
  actor_id: string
}

export interface ActorFled extends EventBase {
  type: 'ActorFled'
  actor_id: string
}

export interface ProtectionStarted extends EventBase {
  type: 'ProtectionStarted'
  protector_id: string
  target_id: string
}

export interface ProtectionEnded extends EventBase {
  type: 'ProtectionEnded'
  protector_id: string
  target_id: string
  reason: 'expired' | 'protector_fell'
}

export interface CombatEnded extends EventBase {
  type: 'CombatEnded'
  winner: string | null
}

// --- game/events.py ---

export interface PlayerActed extends EventBase {
  type: 'PlayerActed'
  actor_id: string
  text: string
}

export interface Narrated extends EventBase {
  type: 'Narrated'
  text: string
}

export interface SceneChanged extends EventBase {
  type: 'SceneChanged'
  scene_id: string
  previous: string | null
}

export interface Revealed extends EventBase {
  type: 'Revealed'
  entity_id: string
  kind: RevealedKind
}

export interface NpcAttitudeChanged extends EventBase {
  type: 'NpcAttitudeChanged'
  npc_id: string
  attitude: Attitude
}

/** NPC переместился в другую сцену: авторитетное присутствие (state.npc_locations). */
export interface NpcMoved extends EventBase {
  type: 'NpcMoved'
  npc_id: string
  scene_id: string
  from_scene?: string | null
}

/** NPC погиб (бой или рассказ ведущего): авторитетный статус (state.npc_status). */
export interface NpcDied extends EventBase {
  type: 'NpcDied'
  npc_id: string
}

export interface ItemGranted extends EventBase {
  type: 'ItemGranted'
  actor_id: string
  item_id: string | null
  name: string
  // Отсутствуют в журналах, записанных до подэтапа 4.
  quantity?: number
  enhancement?: number
}

export interface ItemEquipped extends EventBase {
  type: 'ItemEquipped'
  actor_id: string
  item_id: string
  name: string
  slot: ItemSlot
  /** Имя предмета, снятого с этого слота (остаётся в рюкзаке). */
  replaced: string | null
}

export interface ItemUnequipped extends EventBase {
  type: 'ItemUnequipped'
  actor_id: string
  item_id: string
  name: string
}

export interface ItemUsed extends EventBase {
  type: 'ItemUsed'
  actor_id: string
  item_id: string
  name: string
  effect: ItemUseKind
  rounds: number | null
  spell: string | null
}

export interface ItemEnhanced extends EventBase {
  type: 'ItemEnhanced'
  actor_id: string
  item_id: string
  name: string
  enhancement: number
}

export interface FactNoted extends EventBase {
  type: 'FactNoted'
  text: string
}

export interface ActorsJoined extends EventBase {
  type: 'ActorsJoined'
  actors: ActorState[]
}

export interface EncounterStarted extends EventBase {
  type: 'EncounterStarted'
  encounter_id: string
}

export interface SummaryUpdated extends EventBase {
  type: 'SummaryUpdated'
  text: string
  through_turn: number
}

/** TPK: все герои без сознания/мертвы — маркер поражения (public). */
export interface PartyDefeated extends EventBase {
  type: 'PartyDefeated'
}

export type JournalEvent =
  | DiceRolled
  | CheckResolved
  | ContestResolved
  | AttackResolved
  | DamageDealt
  | Healed
  | ActorDied
  | ResourceChanged
  | ConditionApplied
  | ConditionRemoved
  | TimePassed
  | Rested
  | CombatStarted
  | RoundStarted
  | TurnStarted
  | TurnSlotUsed
  | TurnEnded
  | ActorFled
  | ProtectionStarted
  | ProtectionEnded
  | CombatEnded
  | PlayerActed
  | Narrated
  | SceneChanged
  | Revealed
  | NpcAttitudeChanged
  | NpcMoved
  | NpcDied
  | ItemGranted
  | ItemEquipped
  | ItemUnequipped
  | ItemUsed
  | ItemEnhanced
  | FactNoted
  | ActorsJoined
  | EncounterStarted
  | SummaryUpdated
  | PartyDefeated

export type JournalEventType = JournalEvent['type']

/** Журнальная запись: номер и видимость добавляет журнал (EventOut). */
export interface JournalEntry {
  seq: number
  type: string
  visibility: Visibility
  payload: Record<string, unknown>
}

/** Типизированный доступ к payload записи; неизвестные типы остаются generic. */
export function eventOf<T extends JournalEvent['type']>(
  entry: JournalEntry,
  type: T,
): Extract<JournalEvent, { type: T }> | null {
  if (entry.type !== type || entry.payload.type !== type) return null
  return entry.payload as unknown as Extract<JournalEvent, { type: T }>
}

// --- WS protocol (realtime/ws.py + broadcast.py) ---

/** Эфемерный chunk стриминга: без seq, не журналируется, не досылается. */
export interface NarrationChunk {
  type: 'narration_chunk'
  payload: { text: string }
}

/** Причина мута места (#37): лидерская модерация или нокаут в бою. */
export type MuteSource = 'leader' | 'knockout' | null

/** Эфемерное сообщение чата вне игры (#37): без seq, история только через REST. */
export interface ChatMessageEvent {
  type: 'chat_message'
  payload: {
    id: string
    user_id: string
    author: string
    text: string
    at: string
  }
}

export type RoomMessage = JournalEntry | NarrationChunk | ChatMessageEvent

export function isJournalEntry(message: RoomMessage): message is JournalEntry {
  return typeof (message as JournalEntry).seq === 'number'
}

/** События, после которых имеет смысл перечитать состояние/боевые опции. */
export const STATE_EVENT_TYPES: ReadonlySet<string> = new Set([
  'ActorsJoined', 'ItemGranted', 'SceneChanged', 'Revealed', 'NpcAttitudeChanged', 'NpcMoved',
  'NpcDied',
  'ItemEquipped', 'ItemUnequipped', 'ItemUsed', 'ItemEnhanced',
  'DamageDealt', 'Healed', 'ActorDied', 'ActorFled', 'ResourceChanged',
  'ConditionApplied', 'ConditionRemoved', 'Rested', 'TimePassed',
  'CombatStarted', 'CombatEnded', 'RoundStarted', 'TurnStarted', 'TurnEnded', 'TurnSlotUsed',
  'ProtectionStarted', 'ProtectionEnded', 'PartyDefeated',
])

export const COMBAT_REFETCH_TYPES: ReadonlySet<string> = new Set([
  'EncounterStarted', 'CombatStarted', 'CombatEnded', 'DiceRolled', 'CheckResolved',
  'AttackResolved', 'RoundStarted', 'TurnStarted', 'TurnEnded', 'ActorDied', 'ActorFled',
  'ProtectionStarted', 'ProtectionEnded',
])

/** Состояния, блокирующие команды (ruleset dnd5e-srd: prevents_actions). */
export const PREVENTS_ACTIONS_CONDITIONS: ReadonlySet<string> = new Set([
  'incapacitated', 'paralyzed', 'petrified', 'stunned', 'unconscious',
])