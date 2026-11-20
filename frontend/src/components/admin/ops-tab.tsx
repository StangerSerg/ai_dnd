// Admin «Операционка» tab (task #30, wave 2): ops health (db/redis/queue/worker),
// balance watcher (state, manual run, alert ack) and per-day analytics. All
// endpoints are admin-only (ops.py AdminDep); watcher run is a free balance
// fetch, not an LLM call. active_users is honest «logins/seats per UTC day».
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { admin } from '@/api'
import type { WatcherAlert } from '@/api/admin'
import { QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

const DAYS_OPTIONS = [7, 14, 30, 90] as const

function HealthBadge({ status, testId }: { status: string; testId: string }) {
  const variant = status === 'ok' ? 'outline' : status === 'unknown' ? 'secondary' : 'destructive'
  return <Badge variant={variant} data-testid={testId}>{status}</Badge>
}

function HealthCard() {
  const { t, i18n } = useTranslation()
  const query = useQuery({ queryKey: ['admin', 'ops', 'health'], queryFn: admin.getOpsHealth })
  const health = query.data
  return (
    <Card data-testid="ops-health-card">
      <CardHeader>
        <CardTitle className="font-serif text-xl">{t('admin:ops.healthTitle')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="ops-health-error" />}
        {health && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="ops-health-db">
              <p className="font-medium">{t('admin:ops.db')}</p>
              <HealthBadge status={health.db.status} testId="ops-db-status" />
              {health.db.detail && <p className="text-xs text-muted-foreground">{health.db.detail}</p>}
            </div>
            <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="ops-health-redis">
              <p className="font-medium">{t('admin:ops.redis')}</p>
              <HealthBadge status={health.redis.status} testId="ops-redis-status" />
              {health.redis.detail && <p className="text-xs text-muted-foreground">{health.redis.detail}</p>}
            </div>
            <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="ops-health-queue">
              <p className="font-medium">{t('admin:ops.queue')}</p>
              <HealthBadge status={health.queue.status} testId="ops-queue-status" />
              <p className="text-xs text-muted-foreground">
                {t('admin:ops.queueDetail', {
                  stream: health.queue.stream,
                  depth: health.queue.depth ?? '—',
                  pending: health.queue.pending ?? '—',
                })}
              </p>
            </div>
            <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="ops-health-worker">
              <p className="font-medium">{t('admin:ops.worker')}</p>
              <HealthBadge status={health.worker.status} testId="ops-worker-status" />
              <p className="text-xs text-muted-foreground" data-testid="ops-worker-note">{health.worker.note}</p>
            </div>
            <div className="flex flex-col gap-1 rounded-md border p-3 sm:col-span-2" data-testid="ops-health-turns">
              <p className="font-medium">{t('admin:ops.turns')}</p>
              <p className="text-muted-foreground" data-testid="ops-turn-windows">
                {t('admin:ops.activeTurnWindows', { count: health.active_turn_windows ?? '—' })}
              </p>
              <p className="text-muted-foreground" data-testid="ops-last-ok-turn">
                {t('admin:ops.lastOkTurn', {
                  at: health.last_ok_turn_at ? formatDateTime(health.last_ok_turn_at, i18n.language) : '—',
                })}
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

type WatcherPending = { kind: 'run' } | { kind: 'ack'; alert: WatcherAlert }

function WatcherActionDialog({ pending, onClose }: { pending: WatcherPending; onClose: (notice?: string) => void }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      if (pending.kind === 'run') return admin.runWatcher()
      return admin.ackAlert(pending.alert.id)
    },
    onSuccess: data => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'ops', 'watcher'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
      if (pending.kind === 'run' && 'checked_at' in data) {
        onClose(t('admin:ops.runDone', { status: data.status, balance: data.balance_usd ?? '—' }))
      } else {
        onClose(t('admin:ops.ackDone'))
      }
    },
    onError: err => setError(errorText(err)),
  })

  const isRun = pending.kind === 'run'
  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent data-testid={isRun ? 'watcher-run-dialog' : 'watcher-ack-dialog'}>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">
            {t(isRun ? 'admin:ops.runDialogTitle' : 'admin:ops.ackDialogTitle')}
          </DialogTitle>
          <DialogDescription>
            {isRun ? t('admin:ops.runDialogHint') : t('admin:ops.ackDialogHint', { message: pending.alert.message })}
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="watcher-action-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onClose()} data-testid={isRun ? 'watcher-run-cancel' : 'watcher-ack-cancel'}>
            {t('cancel')}
          </Button>
          <Button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()}
            data-testid={isRun ? 'watcher-run-confirm' : 'watcher-ack-confirm'}>
            {t('admin:ops.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WatcherCard() {
  const { t, i18n } = useTranslation()
  const [pending, setPending] = useState<WatcherPending | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const query = useQuery({ queryKey: ['admin', 'ops', 'watcher'], queryFn: admin.getWatcher })
  const watcher = query.data

  return (
    <Card data-testid="ops-watcher-card">
      <CardHeader>
        <CardTitle className="font-serif text-xl">{t('admin:ops.watcherTitle')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="ops-watcher-error" />}
        {notice && <p role="status" className="text-muted-foreground" data-testid="watcher-notice">{notice}</p>}
        {watcher && (
          <>
            <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="watcher-last">
              <p className="font-medium">{t('admin:ops.lastCheck')}</p>
              {watcher.last ? (
                <div className="flex flex-wrap items-center gap-2 text-muted-foreground" data-testid="watcher-last-data">
                  <HealthBadge status={watcher.last.status} testId="watcher-last-status" />
                  <span>{formatDateTime(watcher.last.checked_at, i18n.language)}</span>
                  <span data-testid="watcher-balance">{t('admin:ops.balance', { usd: watcher.last.balance_usd ?? '—' })}</span>
                  {watcher.last.low && <Badge variant="destructive" data-testid="watcher-low">{t('admin:ops.low')}</Badge>}
                  {watcher.last.message && <span className="text-xs">{watcher.last.message}</span>}
                </div>
              ) : (
                <p className="text-muted-foreground" data-testid="watcher-last-empty">{t('admin:ops.noChecks')}</p>
              )}
              <p className="text-xs text-muted-foreground">
                {t('admin:ops.watcherConfig', { usd: watcher.alert_usd, interval: watcher.interval_s })}
              </p>
            </div>

            <Button variant="outline" className="self-start" data-testid="watcher-run-open"
              onClick={() => { setNotice(null); setPending({ kind: 'run' }) }}>
              {t('admin:ops.runAction')}
            </Button>

            <div className="flex flex-col gap-2" data-testid="watcher-alerts">
              <p className="font-medium">{t('admin:ops.alertsTitle', { count: watcher.alerts.length })}</p>
              {watcher.alerts.length === 0 && (
                <p className="text-muted-foreground" data-testid="watcher-alerts-empty">{t('admin:ops.alertsEmpty')}</p>
              )}
              {watcher.alerts.map(alert => (
                <div key={alert.id} className="flex flex-wrap items-center gap-2 rounded-md border p-3"
                  data-testid="watcher-alert" data-alert-id={alert.id}>
                  <Badge variant="destructive">{alert.kind}</Badge>
                  <span className="min-w-0 flex-1">{alert.message}</span>
                  <span className="text-xs text-muted-foreground">{formatDateTime(alert.at, i18n.language)}</span>
                  <Button variant="outline" size="sm" data-testid="watcher-ack-open"
                    onClick={() => { setNotice(null); setPending({ kind: 'ack', alert }) }}>
                    {t('admin:ops.ackAction')}
                  </Button>
                </div>
              ))}
            </div>
          </>
        )}
      </CardContent>
      {pending && (
        <WatcherActionDialog
          key={pending.kind === 'ack' ? `ack-${pending.alert.id}` : 'run'}
          pending={pending}
          onClose={message => { setPending(null); if (message) setNotice(message) }}
        />
      )}
    </Card>
  )
}

function AnalyticsCard() {
  const { t } = useTranslation()
  const [days, setDays] = useState<number>(7)
  const query = useQuery({
    queryKey: ['admin', 'ops', 'analytics', days],
    queryFn: () => admin.getAnalytics(days),
  })
  const items = query.data?.items ?? []

  return (
    <Card data-testid="ops-analytics-card">
      <CardHeader>
        <CardTitle className="font-serif text-xl">{t('admin:ops.analyticsTitle')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <div className="flex w-40 flex-col gap-2">
          <Label htmlFor="admin-ops-days">{t('admin:ops.days')}</Label>
          <select id="admin-ops-days" className={selectClass} value={days}
            onChange={event => setDays(Number(event.target.value))} data-testid="analytics-days-select">
            {DAYS_OPTIONS.map(value => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="analytics-active-users-hint">{t('admin:ops.activeUsersHint')}</p>
        {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="ops-analytics-error" />}
        {query.isSuccess && (
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="analytics-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:ops.day')}</th>
                  <th className={thClass}>{t('admin:ops.aiCost')}</th>
                  <th className={thClass}>{t('admin:ops.llmCalls')}</th>
                  <th className={thClass}>{t('admin:ops.llmErrors')}</th>
                  <th className={thClass}>{t('admin:ops.activeUsers')}</th>
                  <th className={thClass}>{t('admin:ops.activeSessions')}</th>
                  <th className={thClass}>{t('admin:ops.narratedEvents')}</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr><td className={tdClass} colSpan={7} data-testid="analytics-empty">{t('admin:ops.analyticsEmpty')}</td></tr>
                )}
                {items.map(item => (
                  <tr key={item.day} data-testid="analytics-row" data-day={item.day}>
                    <td className={`${tdClass} whitespace-nowrap`}>{item.day}</td>
                    <td className={tdClass}>{item.ai_cost_usd}</td>
                    <td className={tdClass}>{item.llm_calls}</td>
                    <td className={tdClass}>{item.llm_errors}</td>
                    <td className={tdClass} data-testid="analytics-active-users">{item.active_users}</td>
                    <td className={tdClass}>{item.active_sessions}</td>
                    <td className={tdClass}>{item.narrated_events}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function OpsTab() {
  return (
    <section className="flex flex-col gap-4" data-testid="ops-tab">
      <HealthCard />
      <WatcherCard />
      <AnalyticsCard />
    </section>
  )
}