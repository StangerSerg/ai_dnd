import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { CopyIcon, EyeIcon, FeatherIcon, HeartCrackIcon, LogOutIcon, ScrollTextIcon } from 'lucide-react'
import { ApiError, api, apiErrorMessage, portal, sessionStateView, type ActionResponse, type SeatInfo } from '@/api'
import { COMBAT_REFETCH_TYPES, PREVENTS_ACTIONS_CONDITIONS, STATE_EVENT_TYPES, eventOf, type ActorState, type JournalEntry, type MuteSource } from '@/api/events'
import type { ChatMessage } from '@/api/portal'
import { useKnownSessions } from '@/stores/known-sessions'
import { useDiceReveal } from '@/stores/dice-reveal'
import { useSettings } from '@/stores/settings'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ActionForm } from '@/game/action-form'
import { AvatarImage } from '@/game/avatar'
import { CharacterCard } from '@/game/character-card'
import { ChatPanel } from '@/game/chat-panel'
import { CombatPanel } from '@/game/combat-panel'
import { DefeatDialog } from '@/game/defeat-dialog'
import { EventFeed } from '@/game/event-feed'
import { HostLeaveDialog, hostLeaveErrorText, type HostLeaveTarget } from '@/game/host-leave-dialog'
import {
  KickConfirmDialog,
  KickedBanner,
  MutedBadge,
  SeatControls,
  kickSeat,
  leaveSeat,
  moderationErrorText,
  muteSeat,
} from '@/game/leader-moderation'
import { PartyPanel, type PartyMember } from '@/game/party-panel'
import { SheetView } from '@/game/sheet-view'
import { useSessionSocket, type HostTransferred, type SeatUpdate } from '@/game/use-session-socket'
import '@/demo/demo.css'
import '@/game/game.css'

// Player screen 3.4: assembled from the Codex demo components (src/game/*,
// extracted from src/demo/DemoApp.tsx) over a real PortalSource-style adapter:
// REST (api/portal) + the room WebSocket. /demo keeps the mock source.

const ABILITIES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const

// Зеркало backend SPECTATOR_LIMIT: только для подписи счётчика зрителей.
const SPECTATOR_LIMIT = 20

/** Актёр без сознания: смерть, hp≤0 или prevents_actions-состояние (#37). */
function isActorDowned(actor: ActorState | undefined): boolean {
  return Boolean(actor && (
    actor.dead
    || (actor.resources.hp !== undefined && actor.resources.hp.current <= 0)
    || actor.conditions.some(condition => PREVENTS_ACTIONS_CONDITIONS.has(condition.id))
  ))
}

/** Баннер нокаута (#37): команды недоступны, чат открыт, отряд может поднять. */
function DownedBanner() {
  const { t } = useTranslation()
  return (
    <section className="downed-banner" role="alert" data-testid="downed-banner">
      <span className="downed-banner-icon"><HeartCrackIcon size={22} aria-hidden="true" /></span>
      <div>
        <strong>{t('game:downed.bannerTitle')}</strong>
        <p data-testid="downed-hint">{t('game:downed.bannerHint')}</p>
      </div>
    </section>
  )
}

/** Баннер закрытого стола: ведущий остановил сессию, лента остаётся читаемой. */
function StoppedBanner() {
  const { t } = useTranslation()
  return (
    <section className="kicked-banner stopped-banner" role="alert" data-testid="stopped-banner">
      <div>
        <strong>{t('game:stopped.bannerTitle')}</strong>
        <p>{t('game:stopped.bannerHint')}</p>
      </div>
      <Link to="/games" className="primary-button kicked-to-hall" data-testid="stopped-to-hall">
        {t('game:moderation.kickedToHall')}
      </Link>
    </section>
  )
}

export function GamePage() {
  const { sessionId } = useParams({ from: '/games/$sessionId' })
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const known = useKnownSessions(state => state.sessions.find(item => item.id === sessionId))
  // «Меньше анимации» (3.5): принудительный 2D-кубик, персистится в localStorage.
  const lessAnimation = useSettings(state => state.lessAnimation)
  const toggleLessAnimation = useSettings(state => state.toggleLessAnimation)

  const [action, setAction] = useState('')
  const [submitState, setSubmitState] = useState<ActionResponse | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [turnError, setTurnError] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  // Модерация лидера (#26): оптимистичные муты, удалённые места, свой кик.
  const [mutedOverride, setMutedOverride] = useState<Record<string, boolean>>({})
  // Причина мута (#37): leader | knockout | null; оптимистично и из seat_update.
  const [muteSourceOverride, setMuteSourceOverride] = useState<Record<string, MuteSource>>({})
  const [removedSeats, setRemovedSeats] = useState<ReadonlySet<string>>(new Set())
  const [kicked, setKicked] = useState(false)
  const [moderationBusy, setModerationBusy] = useState<ReadonlySet<string>>(new Set())
  const [moderationError, setModerationError] = useState<string | null>(null)
  const [kickTarget, setKickTarget] = useState<{ id: string; name: string } | null>(null)
  const [kickError, setKickError] = useState<string | null>(null)
  // Добровольный выход из-за стола: confirm-диалог, busy и ошибка API.
  const [leaveOpen, setLeaveOpen] = useState(false)
  const [leaveBusy, setLeaveBusy] = useState(false)
  const [leaveError, setLeaveError] = useState<string | null>(null)
  // Уход в процессе: свой seat_update removed не должен включать KickedBanner.
  const leavingRef = useRef(false)
  // Выход ведущего (передача ведения / закрытие стола): confirm-диалог.
  const [hostLeaveOpen, setHostLeaveOpen] = useState(false)
  const [hostLeaveBusy, setHostLeaveBusy] = useState(false)
  const [hostLeaveError, setHostLeaveError] = useState<string | null>(null)
  // Передача ведения в процессе: removed-места не должны включать KickedBanner.
  const transferringRef = useRef(false)
  // Форс-reconnect повышенного игрока: сервер закрыл его сокет кодом 4403.
  const [reconnectNonce, setReconnectNonce] = useState(0)
  // Стол закрыт ведущим (WS session_stopped) или уже stopped в снапшоте.
  const [stoppedNotice, setStoppedNotice] = useState(false)
  // Поражение отряда (TPK): модалку можно свернуть в баннер и открыть заново.
  const [defeatDismissed, setDefeatDismissed] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  // Последнее WS-сообщение чата вне игры (#37).
  const [chatLive, setChatLive] = useState<ChatMessage | null>(null)
  const refetchTimer = useRef<number | null>(null)

  const sessionQuery = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => api.getSession(sessionId),
    refetchOnWindowFocus: false,
  })
  const session = sessionQuery.data
  const state = useMemo(() => (session ? sessionStateView(session) : null), [session])
  const seat = session?.seat ?? null
  const isPlayer = seat?.role === 'player'
  const isLeader = seat?.role === 'dm'
  const isSpectator = seat?.role === 'spectator'

  useEffect(() => {
    setMutedOverride({})
    setMuteSourceOverride({})
    setRemovedSeats(new Set())
    setKicked(false)
    setModerationBusy(new Set())
    setModerationError(null)
    setKickTarget(null)
    setKickError(null)
    setChatLive(null)
    setLeaveOpen(false)
    setLeaveBusy(false)
    setLeaveError(null)
    leavingRef.current = false
    setHostLeaveOpen(false)
    setHostLeaveBusy(false)
    setHostLeaveError(null)
    transferringRef.current = false
    setReconnectNonce(0)
    setStoppedNotice(false)
    setDefeatDismissed(false)
    setRestartError(null)
  }, [sessionId])

  // Journal events drive state/combat refetches; Narrated ends the pending notice.
  function handleJournal(entry: JournalEntry) {
    if (entry.type === 'Narrated') setSubmitState(null)
    if (kicked) return
    if (STATE_EVENT_TYPES.has(entry.type)) {
      if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current)
      refetchTimer.current = window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      }, 400)
    }
    if (COMBAT_REFETCH_TYPES.has(entry.type)) {
      void queryClient.invalidateQueries({ queryKey: ['combat-options', sessionId] })
    }
  }

  // Эфемерный seat_update: идемпотентно обновляет мут/причину/удаление места.
  function handleSeatUpdate(update: SeatUpdate) {
    if (update.removed) {
      setRemovedSeats(previous => previous.has(update.seat_id) ? previous : new Set(previous).add(update.seat_id))
      if (seat?.id === update.seat_id && !leavingRef.current && !transferringRef.current) setKicked(true)
    } else {
      setMutedOverride(previous => previous[update.seat_id] === update.muted
        ? previous
        : { ...previous, [update.seat_id]: update.muted })
      const source: MuteSource = update.muted ? update.mute_source ?? null : null
      setMuteSourceOverride(previous => previous[update.seat_id] === source
        ? previous
        : { ...previous, [update.seat_id]: source })
    }
    if (!kicked && !leavingRef.current && !transferringRef.current) void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
  }

  // Передача ведения (host_transferred): бэкенд НЕ шлёт seat_update, только
  // этот control, затем закрывает сокеты старого и нового ведущего (4403).
  function handleHostTransferred(payload: HostTransferred) {
    const myUserId = seat?.user_id ?? null
    if (myUserId && payload.new_host_user_id === myUserId) {
      // Я — повышенный игрок: рефетч (роль станет dm) + форс-reconnect,
      // иначе терминальный 4403 оставит меня без dm-видимых событий.
      transferringRef.current = true
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      setReconnectNonce(previous => previous + 1)
    } else if (seat?.role === 'dm' || (seat && seat.id === payload.dm_seat_id)) {
      // Я — старый ведущий: места больше нет, ухожу в зал (не кик).
      transferringRef.current = true
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
      void navigate({ to: '/games' })
    } else {
      // Другой игрок/наблюдатель: сокет жив, просто обновляем места/хоста.
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    }
  }

  // Стол закрыт ведущим (session_stopped): оставшиеся клиенты видят закрытый
  // стол; сам ведущий уже уходит в зал по успешному ответу stop.
  function handleSessionStopped() {
    if (transferringRef.current) return
    setStoppedNotice(true)
    void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
    void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
  }

  // TPK-рестарт (session_restarted): лента уже очищена сокетом; перечитываем
  // снапшот с нуля (nonce → reconnect c since=0), новый opening придёт сам.
  function handleSessionRestarted() {
    setSubmitState(null)
    setSubmitError(null)
    setTurnError(null)
    setRestartError(null)
    setDefeatDismissed(false)
    setMutedOverride({})
    setMuteSourceOverride({})
    setStoppedNotice(false)
    void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    void queryClient.invalidateQueries({ queryKey: ['combat-options', sessionId] })
    setReconnectNonce(previous => previous + 1)
  }

  const socket = useSessionSocket(sessionId, session?.events, handleJournal, handleSeatUpdate, setChatLive,
    handleHostTransferred, handleSessionStopped, handleSessionRestarted, reconnectNonce)

  // Click-to-roll: броски из первого снапшота — история (сразу раскрыты);
  // повторные рефетчи не пересеивают, живые броски остаются за кликом.
  const seedDiceReveal = useDiceReveal(state => state.seedHistory)
  useEffect(() => {
    if (!session) return
    seedDiceReveal(sessionId, session.events.filter(entry => entry.type === 'DiceRolled').map(entry => entry.seq))
  }, [sessionId, session, seedDiceReveal])

  // Повышенный игрок после рефетча уже dm — защита от kick-баннера больше не
  // нужна (последующие кики должны показывать баннер как обычно).
  useEffect(() => {
    if (transferringRef.current && seat?.role === 'dm') transferringRef.current = false
  }, [seat])

  function setSeatBusy(seatId: string, busy: boolean) {
    setModerationBusy(previous => {
      const next = new Set(previous)
      if (busy) next.add(seatId)
      else next.delete(seatId)
      return next
    })
  }

  // Мут без диалога: оптимистично, с откатом при ошибке API.
  async function toggleMute(member: SeatInfo) {
    const current = mutedOverride[member.id] ?? member.muted
    const currentSource: MuteSource = member.id in muteSourceOverride
      ? muteSourceOverride[member.id]
      : (member.mute_source as MuteSource | undefined) ?? null
    const next = !current
    // Защита от снятия мута с упавшего актёра: сервер вернёт 422, не шлём запрос.
    if (!next && (currentSource === 'knockout' || isActorDowned(state?.rules.actors[member.actor_id]))) return
    setModerationError(null)
    setMutedOverride(previous => ({ ...previous, [member.id]: next }))
    setMuteSourceOverride(previous => ({ ...previous, [member.id]: next ? 'leader' : null }))
    setSeatBusy(member.id, true)
    try {
      await muteSeat(sessionId, member.id, next)
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    } catch (error) {
      setMutedOverride(previous => ({ ...previous, [member.id]: current }))
      setMuteSourceOverride(previous => ({ ...previous, [member.id]: currentSource }))
      setModerationError(moderationErrorText(error, t))
    } finally {
      setSeatBusy(member.id, false)
    }
  }

  async function confirmKick() {
    if (!kickTarget) return
    setKickError(null)
    setSeatBusy(kickTarget.id, true)
    try {
      await kickSeat(sessionId, kickTarget.id)
      setRemovedSeats(previous => new Set(previous).add(kickTarget.id))
      setKickTarget(null)
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    } catch (error) {
      setKickError(moderationErrorText(error, t))
    } finally {
      setSeatBusy(kickTarget.id, false)
    }
  }

  // Добровольный выход: место удаляется без бана, уходим в зал; сесть снова
  // можно через зал/приглашение. Ошибка API остаётся в диалоге (не закрываем).
  // Наблюдатель уходит через DELETE observe (место spectator, не seat игрока).
  async function confirmLeave() {
    setLeaveBusy(true)
    setLeaveError(null)
    leavingRef.current = true
    try {
      if (isSpectator) await portal.leaveObserve(sessionId)
      else await leaveSeat(sessionId)
      setLeaveOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
      void navigate({ to: '/games' })
    } catch (error) {
      leavingRef.current = false
      const code = error instanceof ApiError ? error.code ?? error.detail : null
      const errorsKey = isSpectator ? 'game:spectator.leaveErrors' : 'game:leaveTableErrors'
      setLeaveError(code
        ? t(`${errorsKey}.${code}`, { defaultValue: apiErrorMessage(error) })
        : t(`${errorsKey}.network`))
    } finally {
      setLeaveBusy(false)
    }
  }

  // Кому можно передать ведение: места игроков с user_id, не удалённые.
  const hostLeaveTargets: HostLeaveTarget[] = useMemo(() => (session?.seats ?? [])
    .filter(member => member.role === 'player' && member.user_id && !removedSeats.has(member.id))
    .map(member => ({
      id: member.id,
      name: member.name ?? state?.rules.actors[member.actor_id]?.name ?? member.actor_id,
    })), [session?.seats, removedSeats, state])

  // Выход ведущего: передача ведения игроку (transfer-host) либо закрытие
  // стола (stop), если игроков нет. Успех → зал; ошибка остаётся в диалоге.
  async function confirmHostLeave(targetSeatId: string | null) {
    setHostLeaveBusy(true)
    setHostLeaveError(null)
    transferringRef.current = true
    try {
      if (targetSeatId) await portal.transferHost(sessionId, targetSeatId)
      else await portal.stopSession(sessionId)
      setHostLeaveOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
      void navigate({ to: '/games' })
    } catch (error) {
      transferringRef.current = false
      setHostLeaveError(hostLeaveErrorText(error, t))
    } finally {
      setHostLeaveBusy(false)
    }
  }

  const myActor = seat && state ? state.rules.actors[seat.actor_id] ?? null : null
  const inCombat = Boolean(state?.rules.combat)
  const tableStopped = stoppedNotice || session?.status === 'stopped'

  // Поражение отряда — derived из ленты (устойчиво к reconnect/снапшоту).
  // Финальный рассказ = последний Narrated перед маркером PartyDefeated.
  const defeatSeq = socket.events.find(entry => entry.type === 'PartyDefeated')?.seq ?? null
  const defeated = defeatSeq !== null
  let defeatFinalText = ''
  let lastNarratedText = ''
  for (const entry of socket.events) {
    if (entry.type !== 'Narrated') continue
    const event = eventOf(entry, 'Narrated')
    if (!event) continue
    lastNarratedText = event.text
    if (defeatSeq === null || entry.seq < defeatSeq) defeatFinalText = event.text
  }
  if (!defeatFinalText) defeatFinalText = lastNarratedText

  // Мут/нокаут своего места (#37): нокаут = hp≤0, prevents_actions-состояние
  // или mute_source=knockout. Чат и чтение при этом остаются доступными.
  const myMuted = seat ? mutedOverride[seat.id] ?? seat.muted : false
  const myMuteSource: MuteSource = seat
    ? (seat.id in muteSourceOverride ? muteSourceOverride[seat.id] : (seat.mute_source as MuteSource | undefined) ?? null)
    : null
  const myDowned = Boolean(isPlayer && myActor && (
    (myActor.resources.hp !== undefined && myActor.resources.hp.current <= 0)
    || myActor.conditions.some(condition => PREVENTS_ACTIONS_CONDITIONS.has(condition.id))
    || myMuteSource === 'knockout'
  ))

  const characterId = seat?.character_id ?? null
  const characterQuery = useQuery({
    queryKey: ['character-sheet', characterId],
    queryFn: () => {
      if (!characterId) throw new Error('no_character')
      return portal.getCharacter(characterId)
    },
    enabled: Boolean(characterId),
  })
  const sheet = characterQuery.data ?? null

  function actorName(actorId: string | null | undefined): string {
    if (!actorId) return '—'
    if (actorId === 'dm') return t('game:dmName')
    const actor = state?.rules.actors[actorId]
    if (actor) return actor.name
    const member = session?.seats.find(item => item.actor_id === actorId)
    return member?.name ?? actorId
  }

  const diceStyleOf = (actorId: string) =>
    session?.seats.find(item => item.actor_id === actorId)?.dice_style

  const submitMutation = useMutation({
    mutationFn: (text: string) => api.submitAction(sessionId, text),
    onSuccess: result => {
      setSubmitState(result)
      setSubmitError(null)
      setAction('')
      void queryClient.invalidateQueries({ queryKey: ['combat-options', sessionId] })
    },
    onError: (error: unknown) => {
      // D1: turns.py sends some codes as plain string detail (no {"code": ...});
      // treat detail as a code candidate, fall back to the raw code/text.
      if (error instanceof ApiError) {
        const code = error.code ?? error.detail
        if (code === 'muted' && seat) setMutedOverride(previous => ({ ...previous, [seat.id]: true }))
        // 403 muted при нокауте объясняем причиной, а не лидерским мутом.
        const key = code === 'muted' && myDowned ? 'incapacitated' : code
        setSubmitError(t(`game:action.errors.${key}`, { defaultValue: code }))
      } else {
        setSubmitError(apiErrorMessage(error))
      }
    },
  })

  const turnMutation = useMutation({
    mutationFn: () => api.triggerTurn(sessionId),
    onSuccess: () => setTurnError(null),
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        const code = error.code ?? error.detail
        setTurnError(t(`game:turnErrors.${code}`, { defaultValue: code }))
      } else {
        setTurnError(apiErrorMessage(error))
      }
    },
  })

  // «Начать сначала» (host-only): успех → лента очистится по session_restarted
  // и диалог закроется сам; 409 session_busy → «Идёт ход, повторите».
  const restartMutation = useMutation({
    mutationFn: () => portal.restartSession(sessionId),
    onSuccess: () => handleSessionRestarted(),
    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        const code = error.code ?? error.detail
        setRestartError(code === 'session_busy'
          ? t('game:defeat.busy')
          : t(`game:defeat.errors.${code}`, { defaultValue: apiErrorMessage(error) }))
      } else {
        setRestartError(t('game:defeat.errors.network'))
      }
    },
  })

  // Newest-first feed: keep the top (controls + latest event) in view while
  // the reader is near the top; don't yank a reader scrolled down to older ones.
  useEffect(() => {
    if (window.scrollY < 260) window.scrollTo({ top: 0 })
  }, [socket.events.length, socket.liveText])

  useEffect(() => () => {
    if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current)
  }, [])

  async function copyInvite() {
    if (!known?.invite_token) return
    const link = `${window.location.origin}/games?invite=${encodeURIComponent(known.invite_token)}`
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      window.prompt(t('game:inviteCopyPrompt'), link)
    }
  }

  if (sessionQuery.isPending) {
    return <p role="status" className="p-10 text-center text-muted-foreground">{t('loading')}</p>
  }
  // После кика GET сессии падает (no_seat), но последние данные сохраняются:
  // лента остаётся читаемой, пока игрок не уйдёт в зал.
  if (!session || !seat || !state) {
    const code = sessionQuery.error instanceof ApiError ? sessionQuery.error.code ?? sessionQuery.error.detail : null
    return (
      <div className="flex flex-col items-center gap-4 p-10" role="alert">
        <p className="text-destructive-foreground">{code ? t(`game:loadErrors.${code}`, { defaultValue: apiErrorMessage(sessionQuery.error) }) : t('game:loadFailed')}</p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => sessionQuery.refetch()}>{t('retry')}</Button>
          <Button asChild variant="outline"><Link to="/games">{t('game:back')}</Link></Button>
        </div>
      </div>
    )
  }

  const scenarioTitle = known?.scenario ?? session.scenario
  const connectionLabel = socket.status === 'open' ? t('game:connection.online')
    : socket.status === 'closed' ? t('game:connection.offline')
    : t('game:connection.connecting')

  const values = sheet?.values ?? {}
  const conceptParts = [values.race, values.char_class, values.level !== undefined ? t('game:level', { level: String(values.level) }) : null]
    .filter(part => typeof part === 'string' && part)
  const hp = myActor?.resources.hp ?? null

  const partyMembers: PartyMember[] = session.seats
    .filter(member => member.role === 'player' && !removedSeats.has(member.id))
    .map(member => {
      const actor = state.rules.actors[member.actor_id]
      const memberHp = actor?.resources.hp
      const isMe = member.actor_id === seat.actor_id
      const portraitImage = isMe ? sheet?.portrait : null
      const memberMuted = mutedOverride[member.id] ?? member.muted
      const memberSource: MuteSource = member.id in muteSourceOverride
        ? muteSourceOverride[member.id]
        : (member.mute_source as MuteSource | undefined) ?? null
      const memberName = member.name ?? actor?.name ?? member.actor_id
      const hasConditions = Boolean(!actor?.dead && actor?.conditions.length)
      const unmuteBlocked = memberMuted && (memberSource === 'knockout' || isActorDowned(actor))
      return {
        key: member.id,
        name: memberName,
        note: actor?.dead ? t('game:party.dead')
          : actor && actor.conditions.length ? actor.conditions.map(condition => t(`game:conditions.${condition.id}`, { defaultValue: condition.id })).join(', ')
          : t('game:party.player'),
        noteTestId: hasConditions ? 'condition-label' : undefined,
        portrait: portraitImage
          ? <span className="portrait portrait-image"><AvatarImage name={member.name ?? ''} image={portraitImage} /></span>
          : <span className="portrait">{(member.name ?? actor?.name ?? '?').slice(0, 1)}</span>,
        right: actor?.dead ? '†' : memberHp ? `${memberHp.current}/${memberHp.max}` : undefined,
        muted: memberMuted,
        badge: memberMuted ? <MutedBadge source={memberSource} /> : undefined,
        controls: isLeader && !isMe ? (
          <SeatControls
            seatId={member.id}
            muted={memberMuted}
            busy={moderationBusy.has(member.id)}
            unmuteBlocked={unmuteBlocked}
            onToggleMute={() => void toggleMute(member)}
            onKick={() => {
              setModerationError(null)
              setKickError(null)
              setKickTarget({ id: member.id, name: memberName })
            }}
          />
        ) : undefined,
      }
    })

  const quickTexts = [
    { key: 'look', text: t('game:quick.look', { lng: session.language }) },
    { key: 'talk', text: t('game:quick.talk', { lng: session.language }) },
    { key: 'move', text: t('game:quick.move', { lng: session.language }) },
    { key: 'search', text: t('game:quick.search', { lng: session.language }) },
  ]

  const actionNotice = inCombat ? t('game:action.combatHint')
    : submitState?.turn_started ? t('game:action.turnStarted')
    : submitState ? t('game:action.pending', { count: submitState.pending ?? 1 })
    : null

  return (
    <div className="app codex-demo screen-game game-screen">
      <main className="game scene">
        <header className="site-header game-header">
          <Link to="/games" className="text-button">← {t('game:back')}</Link>
          <div className="game-header-center">
            <span>{t(`games:dm.${session.dm_mode}`)} · {session.language.toUpperCase()}</span>
            <strong>{scenarioTitle}</strong>
          </div>
          <div className="header-actions">
            <button type="button" className="text-button less-animation-toggle" aria-pressed={lessAnimation}
              title={t('game:settings.lessAnimationHint')} data-testid="less-animation-toggle"
              onClick={toggleLessAnimation}>
              <FeatherIcon size={12} aria-hidden="true" /> {t('game:settings.lessAnimation')}
            </button>
            {/* Заметная кнопка выхода для всех ролей: ведущему — диалог
                передачи ведения/закрытия стола, игроку — обычный выход. */}
            <button type="button" className="leave-table-button" data-testid="leave-table"
              onClick={() => {
                if (isLeader) { setHostLeaveError(null); setHostLeaveOpen(true) }
                else { setLeaveError(null); setLeaveOpen(true) }
              }}>
              <LogOutIcon size={13} aria-hidden="true" /> {isSpectator ? t('game:spectator.leaveConfirm') : t('game:leaveTable')}
            </button>
            {known?.invite_token && (
              <button type="button" className="text-button" onClick={copyInvite} data-testid="copy-invite">
                <CopyIcon size={12} aria-hidden="true" /> {copied ? t('game:inviteCopied') : t('game:inviteCopy')}
              </button>
            )}
            <span className="header-note" data-testid="ws-status"><span className="status-dot" /> {connectionLabel}</span>
          </div>
        </header>
        <div className="game-layout">
          <section className="story-column" aria-labelledby="game-title">
            {kicked && <KickedBanner />}
            {tableStopped && !kicked && <StoppedBanner />}
            {defeated && defeatDismissed && !kicked && !tableStopped && (
              <section className="kicked-banner stopped-banner" role="alert" data-testid="defeat-banner">
                <div><strong>{t('game:defeat.marker')}</strong></div>
                <button type="button" className="primary-button kicked-to-hall" data-testid="defeat-reopen"
                  onClick={() => setDefeatDismissed(false)}>
                  {t('game:defeat.reopen')}
                </button>
              </section>
            )}
            <div className="story-heading">
              <div>
                <div className="eyebrow"><span /> {isSpectator ? t('game:spectatorScreen') : isPlayer ? t('game:playerScreen') : t('game:dmScreen')}</div>
                <h1 id="game-title" tabIndex={-1}>{scenarioTitle}</h1>
                <p>{t('game:scene')}: {state.scene_id}</p>
              </div>
              <div className="chapter-number">{state.turn}<small>{t('game:turn')}</small></div>
            </div>

            {isPlayer && myDowned && !kicked && <DownedBanner />}

            <CombatPanel sessionId={sessionId} rules={state.rules} myActorId={seat.actor_id} actorName={actorName}
              downed={myDowned} kicked={kicked} />

            {/* Управление сверху: игрок не мотается между повествованием и кнопками. */}
            {isPlayer ? (
              <ActionForm
                id="action"
                label={t('game:action.label')}
                value={action}
                onChange={setAction}
                onSubmit={() => submitMutation.mutate(action.trim())}
                placeholder={t('game:action.placeholder')}
                submitLabel={t('game:action.submit')}
                disabled={inCombat || submitMutation.isPending || myMuted || myDowned || kicked || tableStopped}
                maxLength={2000}
                footerLeft={t('game:action.footer')}
                footerRight={t(`games:dm.${session.dm_mode}`)}
                notice={kicked ? t('game:moderation.kickedHint')
                  : tableStopped ? t('game:stopped.actionNotice')
                  : myDowned ? t('game:downed.actionNotice')
                  : myMuted ? t('game:moderation.mutedNotice')
                  : submitError ?? actionNotice}
                quickActions={quickTexts.map(item => ({
                  key: item.key,
                  label: t(`game:quick.${item.key}`),
                  onClick: () => submitMutation.mutate(item.text),
                }))}
              />
            ) : isSpectator ? (
              /* Наблюдатель: только чтение, никаких кнопок действий/хода. */
              <section className="command-panel spectator-panel" aria-label={t('game:spectatorScreen')} data-testid="spectator-panel">
                <h2><EyeIcon size={14} aria-hidden="true" /> {t('game:spectator.title')}</h2>
                <p>{t('game:spectator.hint')}</p>
                {tableStopped && <p className="action-notice" role="status">{t('game:stopped.actionNotice')}</p>}
              </section>
            ) : (
              <section className="command-panel" aria-label={t('game:dmControls')}>
                <h2>{t('game:dmControls')}</h2>
                <p>{t('game:dmControlsHint')}</p>
                <div>
                  <button type="button" className="primary-button" disabled={turnMutation.isPending || tableStopped}
                    onClick={() => turnMutation.mutate()} data-testid="trigger-turn">
                    {t('game:triggerTurn')} <span>→</span>
                  </button>
                </div>
                {tableStopped && <p className="action-notice" role="status">{t('game:stopped.actionNotice')}</p>}
                {turnError && <p className="action-notice" role="alert">{turnError}</p>}
              </section>
            )}

            {/* Лента «новые сверху»: свежее событие сразу под управлением. */}
            <EventFeed entries={socket.events} liveText={socket.liveText} actorName={actorName}
              sessionId={sessionId} myActorId={isPlayer ? seat.actor_id : null} diceStyleOf={diceStyleOf} />
          </section>

          <aside className="party-column">
            <div className="aside-heading"><span>{isSpectator ? t('game:spectator.title') : t('game:hero')}</span><span>✧</span></div>
            {isSpectator ? (
              /* Наблюдатель: нейтральный блок без статов/листа, счётчик зрителей. */
              <CharacterCard
                portrait={null}
                artNote={t('game:spectator.title')}
                kicker={t('game:spectatorScreen')}
                name={seat.name ?? t('game:spectator.title')}
                concept={t('game:spectator.concept')}
                stats={[]}
              >
                <p className="action-notice" data-testid="spectator-count">
                  {t('game:spectator.count', { count: session.spectators, limit: SPECTATOR_LIMIT })}
                </p>
              </CharacterCard>
            ) : (
              <CharacterCard
                portrait={sheet?.portrait ? { name: sheet.name, image: sheet.portrait } : null}
                artNote={t('game:hero')}
                kicker={conceptParts.join(' · ') || t('game:hero')}
                name={sheet?.name ?? seat.name ?? myActor?.name ?? t('game:unnamed')}
                concept={myActor?.dead ? t('game:party.dead') : t('game:party.player')}
                stats={ABILITIES.map(ability => ({
                  key: ability,
                  short: t(`game:ability.${ability}`),
                  value: typeof values[ability] === 'number' ? String(values[ability]) : '—',
                }))}
                hp={hp}
                hpLabel={t('game:hp')}
                skills={(myActor?.skills ?? seat.skills ?? []).map(id => ({
                  id,
                  label: t(`game:skills.${id}`, { defaultValue: id }),
                }))}
              >
                <div className="hero-actions">
                  <button type="button" className="secondary-button" disabled={!sheet}
                    onClick={() => setSheetOpen(true)} data-testid="open-sheet">
                    <ScrollTextIcon size={12} aria-hidden="true" /> {t('game:openSheet')}
                  </button>
                </div>
              </CharacterCard>
            )}
            <PartyPanel
              heading={t('game:party.title')}
              count={t('game:party.count', { count: partyMembers.length })}
              members={partyMembers}
            />
            {moderationError && <p className="moderation-error" role="alert">{moderationError}</p>}
            {/* Чат вне игры (#37): под отрядом, новые сообщения сверху; открыт при
                муте и без сознания, скрыт кикнутому. */}
            {!kicked && (
              <ChatPanel
                sessionId={sessionId}
                myUserId={seat.user_id ?? null}
                liveMessage={chatLive}
                connectNonce={socket.connectNonce}
              />
            )}
          </aside>
        </div>
      </main>

      <Dialog open={sheetOpen} onOpenChange={setSheetOpen}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">{sheet?.name ?? t('game:openSheet')}</DialogTitle>
          </DialogHeader>
          {sheet && <SheetView sheet={sheet} liveResources={myActor?.resources} />}
        </DialogContent>
      </Dialog>

      <KickConfirmDialog
        open={kickTarget !== null}
        name={kickTarget?.name ?? ''}
        busy={kickTarget !== null && moderationBusy.has(kickTarget.id)}
        error={kickError}
        onConfirm={() => void confirmKick()}
        onCancel={() => { setKickTarget(null); setKickError(null) }}
      />

      {/* Выход ведущего: передача ведения игроку либо закрытие стола. */}
      <HostLeaveDialog
        open={hostLeaveOpen}
        targets={hostLeaveTargets}
        busy={hostLeaveBusy}
        error={hostLeaveError}
        onConfirm={targetSeatId => void confirmHostLeave(targetSeatId)}
        onCancel={() => { setHostLeaveOpen(false); setHostLeaveError(null) }}
      />

      {/* Поражение отряда (TPK): финальный рассказ + покинуть стол / начать
          сначала (рестарт только хосту). Leave ведёт в существующие диалоги. */}
      {defeated && !kicked && !tableStopped && (
        <DefeatDialog
          open={!defeatDismissed && !leaveOpen && !hostLeaveOpen}
          finalText={defeatFinalText}
          isHost={isLeader}
          busy={restartMutation.isPending}
          error={restartError}
          onLeave={() => {
            if (isLeader) { setHostLeaveError(null); setHostLeaveOpen(true) }
            else { setLeaveError(null); setLeaveOpen(true) }
          }}
          onRestart={() => { setRestartError(null); restartMutation.mutate() }}
          onDismiss={() => setDefeatDismissed(true)}
        />
      )}

      {/* Добровольный выход из-за стола: без бана, повторная посадка через зал. */}
      <Dialog open={leaveOpen} onOpenChange={next => { if (!next && !leaveBusy) setLeaveOpen(false) }}>
        <DialogContent className="kick-dialog" data-testid="leave-table-dialog">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">{isSpectator ? t('game:spectator.leaveTitle') : t('game:leaveTableTitle')}</DialogTitle>
            <DialogDescription>{isSpectator ? t('game:spectator.leaveBody') : t('game:leaveTableBody')}</DialogDescription>
          </DialogHeader>
          {leaveError && <p className="moderation-error" role="alert" data-testid="leave-table-error">{leaveError}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setLeaveOpen(false)} disabled={leaveBusy} data-testid="leave-table-cancel">
              {t('game:leaveTableCancel')}
            </Button>
            <Button onClick={() => void confirmLeave()} disabled={leaveBusy} data-testid="leave-table-confirm">
              {isSpectator ? t('game:spectator.leaveConfirm') : t('game:leaveTableConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}