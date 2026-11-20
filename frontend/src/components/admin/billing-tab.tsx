// Admin «Биллинг» tab (task #30, wave 1): manual credit adjustment (dialog),
// global USD spend limits (GET/PUT/DELETE /billing/limits) and the fuse report
// (GET /billing/fuses). Per-user/per-session credit limits stay read-only here;
// wave 2 may extend the limits table with editing.
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { admin, type LimitItem, type LimitsResponse } from '@/api'
import { QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

function FusesCard() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: ['admin', 'fuses'], queryFn: admin.getFuses })
  const fuses = query.data
  return (
    <Card data-testid="fuses-card">
      <CardHeader>
        <CardTitle className="font-serif text-xl">{t('admin:billing.fusesTitle')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="fuses-error" />}
        {fuses && (
          <div className="grid gap-3 sm:grid-cols-2">
            {([
              { id: 'daily', limit: fuses.daily_usd_limit, spent: fuses.daily_spent_usd, tripped: fuses.daily_tripped },
              { id: 'monthly', limit: fuses.monthly_usd_limit, spent: fuses.monthly_spent_usd, tripped: fuses.monthly_tripped },
            ] as const).map(fuse => (
              <div key={fuse.id} className="flex flex-col gap-1 rounded-md border p-3" data-testid={`fuse-${fuse.id}`}>
                <p className="font-medium">{t(`admin:billing.${fuse.id}`)}</p>
                <p className="text-muted-foreground">{t('admin:billing.spentOf', { spent: fuse.spent, limit: fuse.limit })}</p>
                {fuse.tripped
                  ? <Badge variant="destructive" className="self-start" data-testid={`fuse-${fuse.id}-tripped`}>{t('admin:billing.tripped')}</Badge>
                  : <Badge variant="outline" className="self-start" data-testid={`fuse-${fuse.id}-ok`}>{t('admin:billing.fuseOk')}</Badge>}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function GlobalLimitsForm({ limits, onNotice }: { limits: LimitsResponse; onNotice: (message: string) => void }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const globalRow = limits.limits.find(item => item.scope === 'global')
  const [dailyUsd, setDailyUsd] = useState(globalRow?.daily_usd ?? String(limits.defaults.global_daily_usd))
  const [monthlyUsd, setMonthlyUsd] = useState(globalRow?.monthly_usd ?? String(limits.defaults.global_monthly_usd))
  const [error, setError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ['admin', 'limits'] })
    void queryClient.invalidateQueries({ queryKey: ['admin', 'fuses'] })
    void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
  }

  const saveMutation = useMutation({
    mutationFn: () => admin.putLimit({
      scope: 'global',
      daily_usd: dailyUsd.trim() === '' ? null : Number(dailyUsd),
      monthly_usd: monthlyUsd.trim() === '' ? null : Number(monthlyUsd),
    }),
    onSuccess: () => { setError(null); onNotice(t('admin:billing.limitsSaved')); invalidate() },
    onError: err => { onNotice(''); setError(errorText(err)) },
  })

  const deleteMutation = useMutation({
    mutationFn: () => admin.deleteLimit('global'),
    onSuccess: () => { setDeleteOpen(false); setError(null); onNotice(t('admin:billing.limitsDeleted')); invalidate() },
    onError: err => { onNotice(''); setError(errorText(err)) },
  })

  function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    for (const value of [dailyUsd, monthlyUsd]) {
      if (value.trim() !== '' && (!Number.isFinite(Number(value)) || Number(value) < 0)) {
        setError(t('admin:validation.usd'))
        return
      }
    }
    saveMutation.mutate()
  }

  return (
    <Card data-testid="global-limits-card">
      <CardHeader>
        <CardTitle className="font-serif text-xl">{t('admin:billing.globalLimits')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p className="text-muted-foreground">{t('admin:billing.globalLimitsHint')}</p>
        <form onSubmit={handleSave} className="flex flex-wrap items-end gap-3" data-testid="limits-form">
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-limit-daily">{t('admin:billing.dailyUsd')}</Label>
            <Input id="admin-limit-daily" value={dailyUsd} inputMode="decimal" className="w-40"
              onChange={event => setDailyUsd(event.target.value)} data-testid="limit-daily-usd" />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-limit-monthly">{t('admin:billing.monthlyUsd')}</Label>
            <Input id="admin-limit-monthly" value={monthlyUsd} inputMode="decimal" className="w-40"
              onChange={event => setMonthlyUsd(event.target.value)} data-testid="limit-monthly-usd" />
          </div>
          <Button type="submit" disabled={saveMutation.isPending} data-testid="limits-save">{t('save')}</Button>
          <Button type="button" variant="outline" disabled={!globalRow || deleteMutation.isPending}
            onClick={() => setDeleteOpen(true)} data-testid="limits-delete">
            {t('admin:billing.deleteLimits')}
          </Button>
        </form>
        {error && <p className="text-destructive-foreground" role="alert" data-testid="limits-error">{error}</p>}

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent data-testid="limits-delete-dialog">
            <DialogHeader>
              <DialogTitle className="font-serif text-2xl">{t('admin:billing.deleteLimitsTitle')}</DialogTitle>
              <DialogDescription>{t('admin:billing.deleteLimitsHint')}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDeleteOpen(false)} data-testid="limits-delete-cancel">{t('cancel')}</Button>
              <Button type="button" variant="destructive" disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate()} data-testid="limits-delete-confirm">
                {t('admin:users.confirm')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}

function LimitsTable({ limits }: { limits: LimitsResponse }) {
  const { t, i18n } = useTranslation()
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className={tableClass} data-testid="limits-table">
        <thead>
          <tr>
            <th className={thClass}>{t('admin:billing.scope')}</th>
            <th className={thClass}>{t('admin:billing.scopeId')}</th>
            <th className={thClass}>{t('admin:billing.dailyCredits')}</th>
            <th className={thClass}>{t('admin:billing.dailyUsd')}</th>
            <th className={thClass}>{t('admin:billing.monthlyUsd')}</th>
            <th className={thClass}>{t('admin:billing.updatedAt')}</th>
          </tr>
        </thead>
        <tbody>
          {limits.limits.length === 0 && (
            <tr><td className={tdClass} colSpan={6} data-testid="limits-empty">{t('admin:billing.noLimits')}</td></tr>
          )}
          {limits.limits.map((item: LimitItem) => (
            <tr key={item.id} data-testid="limit-row" data-scope={item.scope}>
              <td className={tdClass}>
                <Badge variant="secondary">{t(`admin:billing.scopes.${item.scope}`, { defaultValue: item.scope })}</Badge>
              </td>
              <td className={`${tdClass} break-all text-xs text-muted-foreground`}>{item.scope_id}</td>
              <td className={tdClass}>{item.daily_credits ?? '—'}</td>
              <td className={tdClass}>{item.daily_usd ?? '—'}</td>
              <td className={tdClass}>{item.monthly_usd ?? '—'}</td>
              <td className={`${tdClass} whitespace-nowrap`}>{formatDateTime(item.updated_at, i18n.language)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AdjustDialog() {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [userId, setUserId] = useState('')
  const [credits, setCredits] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Lazy: the user picker loads only while the dialog is open.
  const usersQuery = useQuery({
    queryKey: ['admin', 'users', 'adjust-picker'],
    queryFn: () => admin.listUsers({ limit: 200 }),
    enabled: open,
  })

  const mutation = useMutation({
    mutationFn: () => admin.adjustCredits({ user_id: userId, credits: Number(credits), reason: reason.trim() }),
    onSuccess: data => {
      setOpen(false)
      setError(null)
      setNotice(t('admin:billing.adjustDone', { credits: data.credits }))
      setUserId('')
      setCredits('')
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
    },
    onError: err => setError(errorText(err)),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!userId) { setError(t('admin:validation.user')); return }
    const amount = Number(credits)
    if (credits.trim() === '' || !Number.isInteger(amount) || amount === 0) {
      setError(t('admin:validation.credits'))
      return
    }
    if (!reason.trim()) { setError(t('admin:validation.reason')); return }
    mutation.mutate()
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <Dialog open={open} onOpenChange={value => { setOpen(value); if (value) setNotice(null) }}>
        <DialogTrigger asChild>
          <Button data-testid="adjust-open">{t('admin:billing.adjust')}</Button>
        </DialogTrigger>
      <DialogContent data-testid="adjust-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('admin:billing.adjustTitle')}</DialogTitle>
          <DialogDescription>{t('admin:billing.adjustHint')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-adjust-user">{t('admin:billing.user')}</Label>
            {usersQuery.isPending && <p role="status" className="text-sm text-muted-foreground">{t('loading')}</p>}
            <select id="admin-adjust-user" className={selectClass} value={userId}
              onChange={event => setUserId(event.target.value)} data-testid="adjust-user-select">
              <option value="">{t('admin:billing.userSelect')}</option>
              {(usersQuery.data?.users ?? []).map(user => (
                <option key={user.id} value={user.id}>
                  {user.email ?? user.display_name} · {t('admin:billing.creditsShort', { credits: user.credits })}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-adjust-credits">{t('admin:billing.credits')}</Label>
            <Input id="admin-adjust-credits" value={credits} inputMode="numeric" placeholder="100 / -50"
              onChange={event => setCredits(event.target.value)} data-testid="adjust-credits" />
            <p className="text-xs text-muted-foreground">{t('admin:billing.creditsHint')}</p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-adjust-reason">{t('admin:billing.reason')}</Label>
            <Input id="admin-adjust-reason" value={reason} maxLength={500}
              onChange={event => setReason(event.target.value)} data-testid="adjust-reason" />
          </div>
          {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="adjust-error">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending} data-testid="adjust-submit">{t('admin:billing.adjustSubmit')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
      </Dialog>
      {notice && !open && <p role="status" className="text-sm text-muted-foreground" data-testid="adjust-success">{notice}</p>}
    </div>
  )
}

export function BillingTab() {
  const { t } = useTranslation()
  const limitsQuery = useQuery({ queryKey: ['admin', 'limits'], queryFn: admin.getLimits })
  // The form remounts when the global row changes (key=updated_at), so the
  // success notice lives here to survive the remount.
  const [limitsNotice, setLimitsNotice] = useState<string | null>(null)

  return (
    <section className="flex flex-col gap-4" data-testid="billing-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{t('admin:billing.hint')}</p>
        <AdjustDialog />
      </div>
      <FusesCard />
      {limitsQuery.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {limitsQuery.isError && <QueryError error={limitsQuery.error} onRetry={() => limitsQuery.refetch()} testId="limits-load-error" />}
      {limitsQuery.data && (
        <>
          <GlobalLimitsForm
            key={limitsQuery.data.limits.find(item => item.scope === 'global')?.updated_at ?? 'defaults'}
            limits={limitsQuery.data}
            onNotice={message => setLimitsNotice(message || null)}
          />
          {limitsNotice && <p role="status" className="text-sm text-muted-foreground" data-testid="limits-notice">{limitsNotice}</p>}
          <LimitsTable limits={limitsQuery.data} />
        </>
      )}
    </section>
  )
}