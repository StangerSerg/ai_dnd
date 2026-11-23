import { useEffect, useReducer, useRef, useState } from 'react'
import type { ChatMessageEvent, JournalEntry, MuteSource, RoomMessage } from '@/api/events'
import { isJournalEntry } from '@/api/events'
import type { ChatMessage } from '@/api/portal'

// Room socket (realtime/ws.py): cookie auth through the Vite proxy, backlog
// replay via ?since=last_seen, seq dedup, ack for every journal event.
// Ephemeral narration_chunk messages accumulate into liveText (typing effect);
// the final journal Narrated clears it. Chunks are NOT replayed on reconnect —
// the buffer resets and the full text arrives with the backlog Narrated.

export type SocketStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

/** Эфемерное control-сообщение модерации (#25): без seq, не журналируется. */
export interface SeatUpdate {
  seat_id: string
  muted: boolean
  removed: boolean
  /** Причина мута (#37): leader | knockout | null. */
  mute_source?: MuteSource
}

/**
 * Эфемерный control передачи лидерства: без seq, рассылается всем клиентам.
 * В ai-режиме dm_seat_id/removed_seat_id приходят null: новый лидер сохраняет
 * своё место игрока, прежний лидер тоже остаётся игроком — никто не уходит.
 * В human-режиме (и старых столах) ids непустые.
 */
export interface HostTransferred {
  session_id: string
  dm_seat_id: string | null
  new_host_user_id: string
  removed_seat_id: string | null
}

/** Эфемерный control остановки стола ведущим: без seq. */
export interface SessionStopped {
  session_id: string
  stopped: boolean
}

/** Эфемерный control TPK-рестарта стола хостом: без seq, журнал уже сброшен. */
export interface SessionRestarted {
  session_id: string
  state_version: number
}

export interface SessionSocket {
  events: JournalEntry[]
  liveText: string
  status: SocketStatus
  lastSeq: number
  /** Растёт при каждом успешном (пере)подключении: чат перечитывает историю. */
  connectNonce: number
}

const wsBase = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '').replace(/^http/, 'ws')

function roomUrl(sessionId: string, since: number): string {
  const path = `/ws/sessions/${encodeURIComponent(sessionId)}?since=${since}`
  if (wsBase) return `${wsBase}${path}`
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export function useSessionSocket(
  sessionId: string | null,
  initialEvents: JournalEntry[] | undefined,
  onJournal?: (entry: JournalEntry) => void,
  onSeatUpdate?: (update: SeatUpdate) => void,
  onChatMessage?: (message: ChatMessage) => void,
  onHostTransferred?: (payload: HostTransferred) => void,
  onSessionStopped?: (payload: SessionStopped) => void,
  onSessionRestarted?: (payload: SessionRestarted) => void,
  /**
   * Форс-переподключение: сервер закрывает сокет повышенного игрока кодом
   * 4403 (терминальный, без авто-reconnect). Инкремент nonce перезапускает
   * connect-эффект, и игрок входит заново уже с dm-местом.
   */
  reconnectNonce = 0,
): SessionSocket {
  const [, bump] = useReducer((value: number) => value + 1, 0)
  const [liveText, setLiveText] = useState('')
  const [status, setStatus] = useState<SocketStatus>('connecting')
  const [connectNonce, setConnectNonce] = useState(0)
  const entriesRef = useRef<Map<number, JournalEntry>>(new Map())
  const lastSeqRef = useRef(0)
  const onJournalRef = useRef(onJournal)
  onJournalRef.current = onJournal
  const onSeatUpdateRef = useRef(onSeatUpdate)
  onSeatUpdateRef.current = onSeatUpdate
  const onChatMessageRef = useRef(onChatMessage)
  onChatMessageRef.current = onChatMessage
  const onHostTransferredRef = useRef(onHostTransferred)
  onHostTransferredRef.current = onHostTransferred
  const onSessionStoppedRef = useRef(onSessionStopped)
  onSessionStoppedRef.current = onSessionStopped
  const onSessionRestartedRef = useRef(onSessionRestarted)
  onSessionRestartedRef.current = onSessionRestarted
  // Отличаем форс-reconnect (тот же стол, вырос nonce) от смены комнаты.
  const prevConnectRef = useRef<{ sessionId: string | null; nonce: number }>({ sessionId: null, nonce: 0 })

  // Seed/merge the HTTP snapshot (GET session events) into the same feed.
  useEffect(() => {
    if (!initialEvents) return
    let changed = false
    for (const entry of initialEvents) {
      if (!entriesRef.current.has(entry.seq)) {
        entriesRef.current.set(entry.seq, entry)
        changed = true
      }
      if (entry.seq > lastSeqRef.current) lastSeqRef.current = entry.seq
    }
    if (changed) bump()
  }, [initialEvents])

  useEffect(() => {
    if (!sessionId) return
    const forcedReconnect = prevConnectRef.current.sessionId === sessionId
      && reconnectNonce !== prevConnectRef.current.nonce
    prevConnectRef.current = { sessionId, nonce: reconnectNonce }
    // New room: drop the previous room's feed before replaying the backlog.
    // Форс-reconnect (host_transferred): ленту сохраняем, backlog перечитываем
    // с нуля и мержим по seq — повышенный игрок получает dm-видимые события.
    if (!forcedReconnect) {
      entriesRef.current = new Map()
      setLiveText('')
    }
    lastSeqRef.current = 0
    bump()

    let socket: WebSocket | null = null
    let retryTimer: number | null = null
    let attempts = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStatus(attempts === 0 ? 'connecting' : 'reconnecting')
      // Reconnect: chunks are not replayed; drop the partial live text.
      if (attempts > 0) setLiveText('')
      const ws = new WebSocket(roomUrl(sessionId, lastSeqRef.current))
      socket = ws
      ws.onopen = () => {
        if (disposed) return
        attempts = 0
        setStatus('open')
        // Чат не досылается по seq: после (пере)подключения история грузится заново.
        setConnectNonce(previous => previous + 1)
      }
      ws.onmessage = message => {
        if (disposed) return
        let data: RoomMessage
        try {
          data = JSON.parse(String(message.data)) as RoomMessage
        } catch {
          return
        }
        if (!isJournalEntry(data)) {
          if (data.type === 'narration_chunk' && typeof data.payload?.text === 'string') {
            setLiveText(previous => previous + data.payload.text)
          } else if (data.type === 'chat_message') {
            const payload = (data as ChatMessageEvent).payload
            if (payload && typeof payload.id === 'string' && typeof payload.text === 'string') {
              onChatMessageRef.current?.(payload)
            }
          } else if ((data as { type: string }).type === 'seat_update') {
            const payload = (data as { payload?: Partial<SeatUpdate> }).payload
            if (payload && typeof payload.seat_id === 'string') {
              const source = payload.mute_source
              onSeatUpdateRef.current?.({
                seat_id: payload.seat_id,
                muted: Boolean(payload.muted),
                removed: Boolean(payload.removed),
                mute_source: source === 'leader' || source === 'knockout' ? source : null,
              })
            }
          } else if ((data as { type: string }).type === 'host_transferred') {
            const payload = (data as { payload?: Partial<HostTransferred> }).payload
            // new_host_user_id обязателен; seat-ids в ai-режиме приходят null.
            if (payload && typeof payload.new_host_user_id === 'string') {
              onHostTransferredRef.current?.({
                session_id: typeof payload.session_id === 'string' ? payload.session_id : sessionId,
                dm_seat_id: typeof payload.dm_seat_id === 'string' ? payload.dm_seat_id : null,
                new_host_user_id: payload.new_host_user_id,
                removed_seat_id: typeof payload.removed_seat_id === 'string' ? payload.removed_seat_id : null,
              })
            }
          } else if ((data as { type: string }).type === 'session_stopped') {
            const payload = (data as { payload?: Partial<SessionStopped> }).payload
            onSessionStoppedRef.current?.({
              session_id: typeof payload?.session_id === 'string' ? payload.session_id : sessionId,
              stopped: payload?.stopped !== false,
            })
          } else if ((data as { type: string }).type === 'session_restarted') {
            const payload = (data as { payload?: Partial<SessionRestarted> }).payload
            // Журнал на сервере сброшен: лента с нуля, backlog — с seq 0.
            entriesRef.current = new Map()
            lastSeqRef.current = 0
            setLiveText('')
            bump()
            onSessionRestartedRef.current?.({
              session_id: typeof payload?.session_id === 'string' ? payload.session_id : sessionId,
              state_version: typeof payload?.state_version === 'number' ? payload.state_version : 0,
            })
          }
          return
        }
        const entry = data
        if (typeof entry.seq !== 'number' || entry.seq <= lastSeqRef.current) return
        lastSeqRef.current = entry.seq
        entriesRef.current.set(entry.seq, entry)
        bump()
        if (entry.type === 'Narrated') setLiveText('')
        ws.send(JSON.stringify({ type: 'ack', seq: entry.seq }))
        onJournalRef.current?.(entry)
      }
      ws.onclose = (event: CloseEvent) => {
        if (disposed) return
        setLiveText('')
        // 4401/4403: вход запрещён (нет места / кик) — переподключение бессмысленно.
        if (event.code === 4401 || event.code === 4403) {
          setStatus('closed')
          return
        }
        setStatus('reconnecting')
        attempts += 1
        const delay = Math.min(10_000, 500 * 2 ** Math.min(attempts, 5))
        retryTimer = window.setTimeout(connect, delay)
      }
      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      disposed = true
      if (retryTimer !== null) window.clearTimeout(retryTimer)
      if (socket) {
        socket.onclose = null
        socket.close()
      }
      setStatus('closed')
    }
    // reconnectNonce: форс-переподключение после host_transferred (4403 не
    // переподключается сам). Кик (4403 без nonce) остаётся терминальным.
  }, [sessionId, reconnectNonce])

  const events = Array.from(entriesRef.current.values()).sort((a, b) => a.seq - b.seq)
  return { events, liveText, status, lastSeq: lastSeqRef.current, connectNonce }
}