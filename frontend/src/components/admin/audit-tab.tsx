// Admin «Аудит» tab (task #30, wave 1): GET /api/admin/audit with the contract
// filters (action, actor_id) and limit/offset pagination. Wave 2 actions
// (supervision/ops) render through the same table with raw-action fallback.
import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { admin } from '@/api'
import { Pager, QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass } from './common'

const PAGE_SIZE = 20

// Core actions from admin/service.py (#27). Unknown actions fall back to the
// raw string, so wave 2 entries stay readable without UI changes.
const CORE_ACTIONS = [
  'user.role_change',
  'user.ban',
  'user.unban',
  'user.reset_sessions',
  'billing.adjust',
  'billing.limit_upsert',
  'billing.limit_delete',
  'session.stop',
  'session.resume',
  'session.message',
  'scenario.visibility',
  'complaint.resolve',
  'ops.watcher_run',
  'ops.alert_ack',
] as const

export function AuditTab() {
  const { t, i18n } = useTranslation()
  const [actionInput, setActionInput] = useState('')
  const [actorInput, setActorInput] = useState('')
  const [action, setAction] = useState('')
  const [actorId, setActorId] = useState('')
  const [offset, setOffset] = useState(0)

  const query = useQuery({
    queryKey: ['admin', 'audit', action, actorId, offset],
    queryFn: () => admin.listAudit({
      limit: PAGE_SIZE,
      offset,
      action: action || undefined,
      actor_id: actorId || undefined,
    }),
  })

  function handleFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAction(actionInput)
    setActorId(actorInput.trim())
    setOffset(0)
  }

  const entries = query.data?.entries ?? []
  const total = query.data?.total ?? 0

  return (
    <section className="flex flex-col gap-4" data-testid="audit-tab">
      <form onSubmit={handleFilter} className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-56 flex-col gap-2">
          <Label htmlFor="admin-audit-action">{t('admin:audit.action')}</Label>
          <select id="admin-audit-action" className={selectClass} value={actionInput}
            onChange={event => setActionInput(event.target.value)} data-testid="audit-action-filter">
            <option value="">{t('admin:audit.actionAll')}</option>
            {CORE_ACTIONS.map(value => (
              <option key={value} value={value}>{t(`admin:actions.${value}`, { defaultValue: value })}</option>
            ))}
          </select>
        </div>
        <div className="flex min-w-72 flex-col gap-2">
          <Label htmlFor="admin-audit-actor">{t('admin:audit.actor')}</Label>
          <Input id="admin-audit-actor" value={actorInput} placeholder="00000000-0000-0000-0000-000000000000"
            onChange={event => setActorInput(event.target.value)} data-testid="audit-actor-filter" />
        </div>
        <Button type="submit" variant="outline" data-testid="audit-filter-submit">{t('admin:audit.apply')}</Button>
      </form>

      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="audit-error" />}

      {query.isSuccess && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="audit-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:audit.at')}</th>
                  <th className={thClass}>{t('admin:audit.action')}</th>
                  <th className={thClass}>{t('admin:audit.actor')}</th>
                  <th className={thClass}>{t('admin:audit.target')}</th>
                  <th className={thClass}>{t('admin:audit.details')}</th>
                </tr>
              </thead>
              <tbody>
                {entries.length === 0 && (
                  <tr><td className={tdClass} colSpan={5} data-testid="audit-empty">{t('admin:audit.empty')}</td></tr>
                )}
                {entries.map(entry => (
                  <tr key={entry.id} data-testid="audit-row" data-action={entry.action}>
                    <td className={`${tdClass} whitespace-nowrap`}>{formatDateTime(entry.at, i18n.language)}</td>
                    <td className={tdClass}>
                      <Badge variant="secondary" data-testid="audit-action">
                        {t(`admin:actions.${entry.action}`, { defaultValue: entry.action })}
                      </Badge>
                    </td>
                    <td className={`${tdClass} break-all text-xs text-muted-foreground`} title={entry.actor_id ?? ''}>
                      {entry.actor_id ?? '—'}
                    </td>
                    <td className={`${tdClass} break-all text-xs`}>
                      <span className="text-muted-foreground">{entry.target_type}:</span> {entry.target_id}
                    </td>
                    <td className={`${tdClass} max-w-96`}>
                      <code className="block break-all text-xs text-muted-foreground">{JSON.stringify(entry.details)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={total} onOffset={setOffset} testIdPrefix="audit" />
        </>
      )}
    </section>
  )
}