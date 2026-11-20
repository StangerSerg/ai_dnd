// Admin «Сессии» tab (task #30, wave 2): GET /api/admin/sessions with
// status/search filters, a detail dialog (seats, setup/state JSON, event log
// with «load more») and stop/resume + system message behind confirm dialogs.
// Admin-only on the backend (supervision.py AdminDep).
import { useState, type FormEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { admin } from '@/api'
import type { AdminSession } from '@/api/admin'
import { JsonBlock, Pager, QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

const PAGE_SIZE = 20
const EVENT_PAGE = 200
const MESSAGE_MAX = 2000

type SessionStatusFilter = '' | 'active' | 'stopped'

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return status === 'stopped'
    ? <Badge variant="destructive" data-testid="session-status">{t('admin:sessions.statusStopped')}</Badge>
    : <Badge variant="outline" data-testid="session-status">{t('admin:sessions.statusActive')}</Badge>
}

type PendingAction = { kind: 'stop' | 'resume' } | { kind: 'message'; text: string }

function SessionActionDialog({ sessionId, action, onClose }: {
  sessionId: string
  action: PendingAction
  onClose: (notice?: string) => void
}) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      if (action.kind === 'message') return admin.sendSessionMessage(sessionId, action.text)
      return admin.stopSession(sessionId, action.kind === 'stop')
    },
    onSuccess: data => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'session-events', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
      if (action.kind === 'message' && 'seq' in data) {
        onClose(t('admin:sessions.messageDone', { seq: data.seq }))
      } else {
        onClose(t(action.kind === 'stop' ? 'admin:sessions.stopDone' : 'admin:sessions.resumeDone'))
      }
    },
    onError: err => setError(errorText(err)),
  })

  const titleKey = action.kind === 'stop' ? 'admin:sessions.stopDialogTitle'
    : action.kind === 'resume' ? 'admin:sessions.resumeDialogTitle'
      : 'admin:sessions.messageDialogTitle'
  const hintKey = action.kind === 'stop' ? 'admin:sessions.stopDialogHint'
    : action.kind === 'resume' ? 'admin:sessions.resumeDialogHint'
      : 'admin:sessions.messageDialogHint'

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent data-testid={`session-${action.kind}-dialog`}>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t(titleKey)}</DialogTitle>
          <DialogDescription>{t(hintKey)}</DialogDescription>
        </DialogHeader>
        {action.kind === 'message' && (
          <pre className="max-h-48 overflow-auto rounded-md border bg-muted/40 px-3 py-2 text-sm whitespace-pre-wrap" data-testid="session-message-preview">
            {action.text}
          </pre>
        )}
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="session-action-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onClose()} data-testid={`session-${action.kind}-cancel`}>{t('cancel')}</Button>
          <Button type="button" variant={action.kind === 'stop' ? 'destructive' : 'default'}
            disabled={mutation.isPending} onClick={() => mutation.mutate()} data-testid={`session-${action.kind}-confirm`}>
            {t('admin:sessions.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SessionDetailDialog({ session, onClose }: { session: AdminSession; onClose: () => void }) {
  const { t } = useTranslation()
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [messageText, setMessageText] = useState('')
  const [messageError, setMessageError] = useState<string | null>(null)

  const detailQuery = useQuery({
    queryKey: ['admin', 'session', session.id],
    queryFn: () => admin.getSession(session.id),
  })

  // Event log without the visibility filter (includes dm events). Pages are
  // appended via since=<last loaded seq>; «load more» fetches the next page.
  const eventsQuery = useInfiniteQuery({
    queryKey: ['admin', 'session-events', session.id],
    queryFn: ({ pageParam }) => admin.getSessionEvents(session.id, { since: pageParam, limit: EVENT_PAGE }),
    initialPageParam: 0,
    getNextPageParam: lastPage => {
      const events = lastPage.events
      if (events.length < EVENT_PAGE) return undefined
      return events[events.length - 1].seq
    },
  })

  const detail = detailQuery.data
  const status = detail?.status ?? session.status
  const events = eventsQuery.data?.pages.flatMap(page => page.events) ?? []

  function handleMessageSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = messageText.trim()
    if (text.length < 1 || text.length > MESSAGE_MAX) {
      setMessageError(t('admin:sessions.messageValidation'))
      return
    }
    setMessageError(null)
    setNotice(null)
    setPending({ kind: 'message', text })
  }

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto" data-testid="session-detail-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('admin:sessions.detailTitle')}</DialogTitle>
          <DialogDescription className="break-all">{session.id}</DialogDescription>
        </DialogHeader>

        {detailQuery.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {detailQuery.isError && <QueryError error={detailQuery.error} onRetry={() => detailQuery.refetch()} testId="session-detail-error" />}

        {detail && (
          <div className="flex flex-col gap-4 text-sm">
            <div className="flex flex-wrap items-center gap-3" data-testid="session-detail-meta">
              <span className="font-medium">{detail.scenario}</span>
              <Badge variant="secondary" data-testid="session-detail-mode">
                {t(`admin:sessions.dmModes.${detail.dm_mode}`, { defaultValue: detail.dm_mode })}
              </Badge>
              <Badge variant="secondary">{detail.language}</Badge>
              <StatusBadge status={detail.status} />
              <span className="text-muted-foreground" data-testid="session-detail-lastseq">{t('admin:sessions.lastSeq', { seq: detail.last_seq })}</span>
            </div>

            <div className="flex flex-wrap gap-2">
              {status === 'stopped' ? (
                <Button variant="outline" data-testid="session-resume-open"
                  onClick={() => { setNotice(null); setPending({ kind: 'resume' }) }}>
                  {t('admin:sessions.resumeAction')}
                </Button>
              ) : (
                <Button variant="destructive" data-testid="session-stop-open"
                  onClick={() => { setNotice(null); setPending({ kind: 'stop' }) }}>
                  {t('admin:sessions.stopAction')}
                </Button>
              )}
            </div>

            {notice && <p role="status" className="text-muted-foreground" data-testid="session-notice">{notice}</p>}

            <div className="overflow-x-auto rounded-md border">
              <table className={tableClass} data-testid="session-seats-table">
                <thead>
                  <tr>
                    <th className={thClass}>{t('admin:sessions.seatRole')}</th>
                    <th className={thClass}>{t('admin:sessions.seatName')}</th>
                    <th className={thClass}>{t('admin:sessions.seatMuted')}</th>
                    <th className={thClass}>{t('admin:sessions.seatEmail')}</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.seats.length === 0 && (
                    <tr><td className={tdClass} colSpan={4} data-testid="session-seats-empty">{t('admin:sessions.seatsEmpty')}</td></tr>
                  )}
                  {detail.seats.map(seat => (
                    <tr key={seat.id} data-testid="session-seat-row" data-seat-id={seat.id}>
                      <td className={tdClass}>{t(`admin:sessions.seatRoles.${seat.role}`, { defaultValue: seat.role })}</td>
                      <td className={tdClass}>{seat.name ?? '—'}</td>
                      <td className={tdClass}>
                        {seat.muted
                          ? <Badge variant="destructive" data-testid="seat-muted">{t('admin:sessions.muted')}</Badge>
                          : <Badge variant="outline" data-testid="seat-unmuted">{t('admin:sessions.notMuted')}</Badge>}
                      </td>
                      <td className={tdClass}>{seat.user_email ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <JsonBlock label={t('admin:sessions.setup')} value={detail.setup} testId="session-setup" />
            <JsonBlock label={t('admin:sessions.state')} value={detail.state} testId="session-state" />

            <form onSubmit={handleMessageSubmit} className="flex flex-col gap-2" data-testid="session-message-form">
              <Label htmlFor="admin-session-message">{t('admin:sessions.messageLabel')}</Label>
              <textarea
                id="admin-session-message"
                className="min-h-20 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                value={messageText}
                maxLength={MESSAGE_MAX}
                placeholder={t('admin:sessions.messagePlaceholder')}
                onChange={event => setMessageText(event.target.value)}
                data-testid="session-message-input"
              />
              <div className="flex items-center gap-3">
                <Button type="submit" variant="outline" disabled={messageText.trim().length === 0} data-testid="session-message-open">
                  {t('admin:sessions.messageAction')}
                </Button>
                <span className="text-xs text-muted-foreground">{messageText.length}/{MESSAGE_MAX}</span>
              </div>
              {messageError && <p className="text-destructive-foreground" role="alert" data-testid="session-message-validation">{messageError}</p>}
            </form>

            <div className="flex flex-col gap-2" data-testid="session-events">
              <p className="font-medium">{t('admin:sessions.eventsTitle', { count: events.length })}</p>
              {eventsQuery.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
              {eventsQuery.isError && <QueryError error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} testId="session-events-error" />}
              {eventsQuery.isSuccess && events.length === 0 && (
                <p className="text-muted-foreground" data-testid="session-events-empty">{t('admin:sessions.eventsEmpty')}</p>
              )}
              <div className="flex max-h-96 flex-col gap-2 overflow-y-auto">
                {events.map(event => (
                  <JsonBlock
                    key={event.seq}
                    label={`#${event.seq} · ${event.type} · ${event.visibility}`}
                    value={event.payload}
                    testId={`session-event-${event.seq}`}
                  />
                ))}
              </div>
              <Button variant="outline" size="sm" className="self-start"
                disabled={!eventsQuery.hasNextPage || eventsQuery.isFetchingNextPage}
                onClick={() => eventsQuery.fetchNextPage()} data-testid="session-events-more">
                {eventsQuery.isFetchingNextPage ? t('loading') : t('admin:sessions.eventsMore')}
              </Button>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} data-testid="session-detail-close">{t('close')}</Button>
        </DialogFooter>

        {pending && (
          <SessionActionDialog
            key={pending.kind}
            sessionId={session.id}
            action={pending}
            onClose={message => {
              setPending(null)
              if (message) {
                setNotice(message)
                if (pending.kind === 'message') setMessageText('')
              }
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

export function SessionsTab() {
  const { t, i18n } = useTranslation()
  const [status, setStatus] = useState<SessionStatusFilter>('')
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<AdminSession | null>(null)

  const query = useQuery({
    queryKey: ['admin', 'sessions', status, q, offset],
    queryFn: () => admin.listSessions({
      status: status || undefined,
      q: q || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
  })

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setQ(searchInput.trim().slice(0, 120))
    setOffset(0)
  }

  const sessions = query.data?.sessions ?? []
  const total = query.data?.total ?? 0

  return (
    <section className="flex flex-col gap-4" data-testid="sessions-tab">
      <form onSubmit={handleSearch} className="flex flex-wrap items-end gap-2">
        <div className="flex w-44 flex-col gap-2">
          <Label htmlFor="admin-sessions-status">{t('admin:sessions.status')}</Label>
          <select id="admin-sessions-status" className={selectClass} value={status}
            onChange={event => { setStatus(event.target.value as SessionStatusFilter); setOffset(0) }}
            data-testid="sessions-status-filter">
            <option value="">{t('admin:sessions.statusAll')}</option>
            <option value="active">{t('admin:sessions.statusActive')}</option>
            <option value="stopped">{t('admin:sessions.statusStopped')}</option>
          </select>
        </div>
        <div className="flex min-w-64 flex-col gap-2">
          <Label htmlFor="admin-sessions-search">{t('admin:sessions.search')}</Label>
          <Input id="admin-sessions-search" value={searchInput} maxLength={120}
            placeholder={t('admin:sessions.searchPlaceholder')}
            onChange={event => setSearchInput(event.target.value)} data-testid="sessions-search" />
        </div>
        <Button type="submit" variant="outline" data-testid="sessions-search-submit">{t('admin:sessions.searchSubmit')}</Button>
      </form>

      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="sessions-error" />}

      {query.isSuccess && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="sessions-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:sessions.scenario')}</th>
                  <th className={thClass}>{t('admin:sessions.host')}</th>
                  <th className={thClass}>{t('admin:sessions.mode')}</th>
                  <th className={thClass}>{t('admin:sessions.language')}</th>
                  <th className={thClass}>{t('admin:sessions.status')}</th>
                  <th className={thClass}>{t('admin:sessions.seats')}</th>
                  <th className={thClass}>{t('admin:sessions.lastSeqCol')}</th>
                  <th className={thClass}>{t('admin:sessions.created')}</th>
                </tr>
              </thead>
              <tbody>
                {sessions.length === 0 && (
                  <tr><td className={tdClass} colSpan={8} data-testid="sessions-empty">{t('admin:sessions.empty')}</td></tr>
                )}
                {sessions.map(session => (
                  <tr key={session.id} className="cursor-pointer hover:bg-accent/50"
                    data-testid="session-row" data-session-id={session.id}
                    onClick={() => setSelected(session)}>
                    <td className={tdClass}>
                      <button type="button" className="text-left font-medium underline-offset-2 hover:underline"
                        data-testid="session-open" onClick={event => { event.stopPropagation(); setSelected(session) }}>
                        {session.scenario}
                      </button>
                    </td>
                    <td className={tdClass}>{session.host_name ?? '—'}</td>
                    <td className={tdClass}>{t(`admin:sessions.dmModes.${session.dm_mode}`, { defaultValue: session.dm_mode })}</td>
                    <td className={tdClass}>{session.language}</td>
                    <td className={tdClass}><StatusBadge status={session.status} /></td>
                    <td className={tdClass} data-testid="session-seats-count">{session.seats_count}</td>
                    <td className={tdClass}>{session.last_seq}</td>
                    <td className={`${tdClass} whitespace-nowrap`}>{formatDateTime(session.created_at, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={total} onOffset={setOffset} testIdPrefix="sessions" />
        </>
      )}

      {selected && (
        <SessionDetailDialog key={selected.id} session={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  )
}