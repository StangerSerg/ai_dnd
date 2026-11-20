// Admin «Жалобы» tab (task #30, wave 2): GET /api/admin/complaints with a
// status filter and pagination; open complaints are resolved/dismissed through
// a confirm dialog with a mandatory resolution text (1..500). Available to
// admin and moderator (supervision.py ModDep).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { admin } from '@/api'
import type { AdminComplaint, ComplaintStatus } from '@/api/admin'
import { Pager, QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

const PAGE_SIZE = 20
const RESOLUTION_MAX = 500

type StatusFilter = '' | 'open' | 'resolved' | 'dismissed'

function ComplaintStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const variant = status === 'open' ? 'destructive' : status === 'resolved' ? 'default' : 'secondary'
  return (
    <Badge variant={variant} data-testid="complaint-status">
      {t(`admin:complaints.statuses.${status}`, { defaultValue: status })}
    </Badge>
  )
}

function ResolveDialog({ complaint, onClose }: { complaint: AdminComplaint; onClose: (notice?: string) => void }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<ComplaintStatus>('resolved')
  const [resolution, setResolution] = useState('')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => admin.resolveComplaint(complaint.id, { status, resolution: resolution.trim() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'complaints'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
      onClose(t('admin:complaints.resolveDone'))
    },
    onError: err => setError(errorText(err)),
  })

  function handleConfirm() {
    const text = resolution.trim()
    if (text.length < 1 || text.length > RESOLUTION_MAX) {
      setError(t('admin:complaints.resolutionValidation'))
      return
    }
    setError(null)
    mutation.mutate()
  }

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent data-testid="resolve-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('admin:complaints.resolveDialogTitle')}</DialogTitle>
          <DialogDescription>
            {t('admin:complaints.resolveDialogHint', { reporter: complaint.reporter_name, scenario: complaint.session_scenario })}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <Label htmlFor="admin-complaint-status">{t('admin:complaints.decision')}</Label>
          <select id="admin-complaint-status" className={selectClass} value={status}
            onChange={event => setStatus(event.target.value as ComplaintStatus)} data-testid="resolve-status-select">
            <option value="resolved">{t('admin:complaints.statuses.resolved')}</option>
            <option value="dismissed">{t('admin:complaints.statuses.dismissed')}</option>
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="admin-complaint-resolution">{t('admin:complaints.resolution')}</Label>
          <textarea
            id="admin-complaint-resolution"
            className="min-h-24 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            value={resolution}
            maxLength={RESOLUTION_MAX}
            placeholder={t('admin:complaints.resolutionPlaceholder')}
            onChange={event => setResolution(event.target.value)}
            data-testid="resolve-resolution-input"
          />
          <span className="text-xs text-muted-foreground">{resolution.length}/{RESOLUTION_MAX}</span>
        </div>
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="resolve-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onClose()} data-testid="resolve-cancel">{t('cancel')}</Button>
          <Button type="button" disabled={mutation.isPending || resolution.trim().length === 0}
            onClick={handleConfirm} data-testid="resolve-confirm">
            {t('admin:complaints.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ComplaintsTab() {
  const { t, i18n } = useTranslation()
  const [status, setStatus] = useState<StatusFilter>('')
  const [offset, setOffset] = useState(0)
  const [pending, setPending] = useState<AdminComplaint | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['admin', 'complaints', status, offset],
    queryFn: () => admin.listComplaints({ status: status || undefined, limit: PAGE_SIZE, offset }),
  })

  const complaints = query.data?.complaints ?? []
  const total = query.data?.total ?? 0

  return (
    <section className="flex flex-col gap-4" data-testid="complaints-tab">
      <div className="flex w-56 flex-col gap-2">
        <Label htmlFor="admin-complaints-status">{t('admin:complaints.status')}</Label>
        <select id="admin-complaints-status" className={selectClass} value={status}
          onChange={event => { setStatus(event.target.value as StatusFilter); setOffset(0) }}
          data-testid="complaints-status-filter">
          <option value="">{t('admin:complaints.statusAll')}</option>
          <option value="open">{t('admin:complaints.statuses.open')}</option>
          <option value="resolved">{t('admin:complaints.statuses.resolved')}</option>
          <option value="dismissed">{t('admin:complaints.statuses.dismissed')}</option>
        </select>
      </div>

      {notice && <p className="text-sm text-muted-foreground" role="status" data-testid="complaints-notice">{notice}</p>}
      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="complaints-error" />}

      {query.isSuccess && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="complaints-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:complaints.reporter')}</th>
                  <th className={thClass}>{t('admin:complaints.session')}</th>
                  <th className={thClass}>{t('admin:complaints.target')}</th>
                  <th className={thClass}>{t('admin:complaints.reason')}</th>
                  <th className={thClass}>{t('admin:complaints.status')}</th>
                  <th className={thClass}>{t('admin:complaints.created')}</th>
                  <th className={thClass}>{t('admin:complaints.resolutionCol')}</th>
                </tr>
              </thead>
              <tbody>
                {complaints.length === 0 && (
                  <tr><td className={tdClass} colSpan={7} data-testid="complaints-empty">{t('admin:complaints.empty')}</td></tr>
                )}
                {complaints.map(complaint => (
                  <tr key={complaint.id} data-testid="complaint-row" data-complaint-id={complaint.id} data-status={complaint.status}>
                    <td className={tdClass}>{complaint.reporter_name}</td>
                    <td className={tdClass}>
                      <span className="block">{complaint.session_scenario}</span>
                      <span className="block break-all text-xs text-muted-foreground">{complaint.session_id}</span>
                    </td>
                    <td className={`${tdClass} text-xs`}>
                      <span className="text-muted-foreground">{complaint.target_type}:</span>{' '}
                      <span className="break-all">{complaint.target_id}</span>
                    </td>
                    <td className={`${tdClass} max-w-72`}>{complaint.reason}</td>
                    <td className={tdClass}><ComplaintStatusBadge status={complaint.status} /></td>
                    <td className={`${tdClass} whitespace-nowrap`}>{formatDateTime(complaint.created_at, i18n.language)}</td>
                    <td className={`${tdClass} max-w-72`}>
                      {complaint.status === 'open' ? (
                        <Button variant="outline" size="sm" data-testid="complaint-resolve-open"
                          onClick={() => { setNotice(null); setPending(complaint) }}>
                          {t('admin:complaints.resolveAction')}
                        </Button>
                      ) : (
                        <div className="flex flex-col gap-1 text-xs" data-testid="complaint-resolution">
                          <span>{complaint.resolution ?? '—'}</span>
                          <span className="text-muted-foreground">
                            {complaint.resolved_by_name ?? '—'}
                            {complaint.resolved_at ? ` · ${formatDateTime(complaint.resolved_at, i18n.language)}` : ''}
                          </span>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={total} onOffset={setOffset} testIdPrefix="complaints" />
        </>
      )}

      {pending && (
        <ResolveDialog
          key={pending.id}
          complaint={pending}
          onClose={message => { setPending(null); if (message) setNotice(message) }}
        />
      )}
    </section>
  )
}