import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUpIcon, MessagesSquareIcon, SendIcon } from 'lucide-react'
import { ApiError } from '@/api/client'
import { portal, type ChatMessage } from '@/api/portal'

// Чат вне игры (#37): REST-история страницами + WS-дополнение без дублей.
// Доступен при муте и без сознания; кикнутому панель не показывается.
// Лента «новые сверху»: свежие сообщения наверху, старые ниже, «Загрузить ещё» внизу.

const PAGE_SIZE = 50
const MAX_TEXT = 500

interface PendingMessage {
  tempId: string
  text: string
  at: string
}

interface DisplayMessage {
  id: string
  author: string
  text: string
  at: string
  mine: boolean
  pending: boolean
}

function mergeById(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const map = new Map(previous.map(message => [message.id, message]))
  let changed = false
  for (const message of incoming) {
    if (!map.has(message.id)) {
      map.set(message.id, message)
      changed = true
    }
  }
  if (!changed) return previous
  return Array.from(map.values()).sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
}

function chatErrorText(error: unknown, t: ReturnType<typeof useTranslation>['t']): string {
  if (error instanceof ApiError) {
    const code = error.code ?? error.detail
    return t(`game:chat.errors.${code}`, { defaultValue: t('game:chat.errors.network') })
  }
  return t('game:chat.errors.network')
}

export function ChatPanel({ sessionId, myUserId, liveMessage, connectNonce }: {
  sessionId: string
  myUserId: string | null
  /** Последнее WS-сообщение чата (без seq): дополнение к REST-истории. */
  liveMessage: ChatMessage | null
  /** Счётчик (пере)подключений сокета: история перечитывается с сервера. */
  connectNonce: number
}) {
  const { t, i18n } = useTranslation()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState<PendingMessage[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [unread, setUnread] = useState(0)

  const scrollRef = useRef<HTMLDivElement>(null)
  const atTopRef = useRef(true)
  const olderAnchorRef = useRef<{ top: number } | null>(null)
  const requestRef = useRef(0)
  const tempCounterRef = useRef(0)
  const olderBusyRef = useRef(false)

  const timeFormat = new Intl.DateTimeFormat(i18n.language === 'ru' ? 'ru-RU' : 'en-GB', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  })

  const scrollToTop = useCallback(() => {
    const element = scrollRef.current
    if (element) element.scrollTop = 0
    atTopRef.current = true
    setUnread(0)
  }, [])

  // Смена стола: полный сброс ленты и черновика.
  useEffect(() => {
    requestRef.current += 1
    olderBusyRef.current = false
    setMessages([])
    setPending([])
    setDraft('')
    setError(null)
    setLoaded(false)
    setHasMore(false)
    setLoadingOlder(false)
    setUnread(0)
    atTopRef.current = true
  }, [sessionId])

  // История: первая загрузка и каждое (пере)подключение сокета (чат не досылается по seq).
  useEffect(() => {
    const requestId = ++requestRef.current
    let disposed = false
    portal.chatHistory(sessionId, { limit: PAGE_SIZE })
      .then(page => {
        if (disposed || requestId !== requestRef.current) return
        setMessages(previous => mergeById(previous, page.messages))
        setHasMore(page.messages.length >= PAGE_SIZE)
        setLoaded(true)
        if (atTopRef.current) scrollToTop()
      })
      .catch(err => {
        if (disposed || requestId !== requestRef.current) return
        setLoaded(true)
        setError(chatErrorText(err, t))
      })
    return () => { disposed = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, connectNonce])

  // WS-дополнение: дедуп по id; оптимистичный дубль своего сообщения снимается.
  useEffect(() => {
    if (!liveMessage) return
    setMessages(previous => mergeById(previous, [liveMessage]))
    setPending(previous => previous.filter(item =>
      !(item.text === liveMessage.text && liveMessage.user_id === myUserId)))
    if (atTopRef.current) scrollToTop()
    else setUnread(previous => previous + 1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMessage])

  // Прокрутка: к верху (новым) только если читатель наверху; после «Загрузить ещё»
  // старые сообщения добавляются внизу, поэтому позиция читателя не смещается.
  useLayoutEffect(() => {
    const element = scrollRef.current
    if (!element) return
    const anchor = olderAnchorRef.current
    if (anchor) {
      element.scrollTop = anchor.top
      olderAnchorRef.current = null
      return
    }
    if (atTopRef.current) element.scrollTop = 0
  }, [messages, pending])

  function handleScroll() {
    const element = scrollRef.current
    if (!element) return
    const atTop = element.scrollTop < 48
    atTopRef.current = atTop
    if (atTop) setUnread(0)
  }

  async function loadOlder() {
    const oldest = messages[0]
    if (!oldest || olderBusyRef.current) return
    olderBusyRef.current = true
    setLoadingOlder(true)
    setError(null)
    const element = scrollRef.current
    if (element) olderAnchorRef.current = { top: element.scrollTop }
    try {
      const page = await portal.chatHistory(sessionId, { limit: PAGE_SIZE, before: oldest.id })
      setMessages(previous => mergeById(previous, page.messages))
      setHasMore(page.messages.length >= PAGE_SIZE)
    } catch (err) {
      olderAnchorRef.current = null
      setError(chatErrorText(err, t))
    } finally {
      olderBusyRef.current = false
      setLoadingOlder(false)
    }
  }

  async function send() {
    const text = draft.trim()
    if (!text || text.length > MAX_TEXT) return
    const tempId = `pending-${++tempCounterRef.current}`
    setDraft('')
    setError(null)
    setPending(previous => [...previous, { tempId, text, at: new Date().toISOString() }])
    atTopRef.current = true
    try {
      const saved = await portal.postChat(sessionId, text)
      setPending(previous => previous.filter(item => item.tempId !== tempId))
      setMessages(previous => mergeById(previous, [saved]))
      scrollToTop()
    } catch (err) {
      // Откат оптимистичной вставки: текст возвращается в поле, ошибка видна.
      setPending(previous => previous.filter(item => item.tempId !== tempId))
      setDraft(current => (current ? `${text} ${current}` : text))
      setError(chatErrorText(err, t))
    }
  }

  // «Новые сверху»: оптимистичные pending — самые свежие, затем история от новых к старым.
  const display: DisplayMessage[] = [
    ...[...pending].reverse().map(item => ({
      id: item.tempId,
      author: '',
      text: item.text,
      at: item.at,
      mine: true,
      pending: true,
    })),
    ...[...messages].reverse().map(message => ({
      id: message.id,
      author: message.author,
      text: message.text,
      at: message.at,
      mine: message.user_id === myUserId,
      pending: false,
    })),
  ]

  return (
    <section className="chat-panel" data-testid="chat-panel" aria-labelledby="chat-title">
      <div className="chat-heading">
        <MessagesSquareIcon size={16} aria-hidden="true" />
        <h2 id="chat-title">{t('game:chat.title')}</h2>
        <span className="chat-heading-tag">{t('game:chat.tag')}</span>
      </div>
      <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll} role="log" aria-live="polite">
        {!loaded && <p className="chat-loading" role="status">{t('loading')}</p>}
        {loaded && display.length === 0 && (
          <p className="chat-empty" data-testid="chat-empty">{t('game:chat.empty')}</p>
        )}
        {display.map(message => (
          <article
            key={message.id}
            className={`chat-message${message.mine ? ' chat-message-mine' : ''}${message.pending ? ' chat-message-pending' : ''}`}
            data-testid="chat-message"
            data-mine={message.mine ? 'true' : 'false'}
            data-pending={message.pending ? 'true' : 'false'}
          >
            <header>
              <strong>{message.mine ? t('game:chat.you') : message.author}</strong>
              <time dateTime={message.at}>{timeFormat.format(new Date(message.at))}</time>
            </header>
            <p>{message.text}</p>
            {message.pending && <small className="chat-sending">{t('game:chat.sending')}</small>}
          </article>
        ))}
        {/* Старые сообщения ниже: догрузка истории — в конце ленты. */}
        {hasMore && (
          <button type="button" className="chat-load-older" data-testid="chat-load-older"
            disabled={loadingOlder} onClick={() => void loadOlder()}>
            {loadingOlder ? t('loading') : t('game:chat.loadOlder')}
          </button>
        )}
      </div>
      {unread > 0 && (
        <button type="button" className="chat-jump" data-testid="chat-jump" onClick={scrollToTop}>
          <ArrowUpIcon size={12} aria-hidden="true" /> {t('game:chat.newMessages', { count: unread })}
        </button>
      )}
      {error && <p className="chat-error" role="alert" data-testid="chat-error">{error}</p>}
      <form className="chat-form" onSubmit={event => { event.preventDefault(); void send() }}>
        <textarea
          data-testid="chat-input"
          rows={2}
          maxLength={MAX_TEXT}
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void send()
            }
          }}
          placeholder={t('game:chat.placeholder')}
          aria-label={t('game:chat.inputLabel')}
        />
        <button type="submit" className="chat-send" data-testid="chat-send"
          disabled={!draft.trim()} aria-label={t('game:chat.send')} title={t('game:chat.send')}>
          <SendIcon size={16} aria-hidden="true" />
        </button>
      </form>
      <div className="chat-footer">
        <span>{t('game:chat.hint')}</span>
        <span className="chat-counter">{draft.length}/{MAX_TEXT}</span>
      </div>
    </section>
  )
}