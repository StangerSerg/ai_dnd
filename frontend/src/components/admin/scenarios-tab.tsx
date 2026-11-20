// Admin «Сценарии» tab (task #30, wave 2): GET /api/admin/scenarios with
// pagination and visibility change (private/unlisted/public) behind a confirm
// dialog. Available to admin and moderator (supervision.py ModDep).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { admin } from '@/api'
import type { AdminScenario, ScenarioVisibility } from '@/api/admin'
import { Pager, QueryError, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

const PAGE_SIZE = 20
const VISIBILITIES: ScenarioVisibility[] = ['private', 'unlisted', 'public']

interface PendingVisibility {
  scenario: AdminScenario
  visibility: ScenarioVisibility
}

function VisibilityDialog({ pending, onClose }: { pending: PendingVisibility; onClose: (notice?: string) => void }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const { scenario, visibility } = pending

  const mutation = useMutation({
    mutationFn: () => admin.setScenarioVisibility(scenario.id, visibility),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'scenarios'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
      onClose(t('admin:scenarios.visibilityDone', { title: scenario.title }))
    },
    onError: err => setError(errorText(err)),
  })

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent data-testid="visibility-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('admin:scenarios.visibilityDialogTitle')}</DialogTitle>
          <DialogDescription>
            {t('admin:scenarios.visibilityDialogHint', {
              title: scenario.title,
              from: t(`admin:scenarios.visibilities.${scenario.visibility}`, { defaultValue: scenario.visibility }),
              to: t(`admin:scenarios.visibilities.${visibility}`),
            })}
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="visibility-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onClose()} data-testid="visibility-cancel">{t('cancel')}</Button>
          <Button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()} data-testid="visibility-confirm">
            {t('admin:scenarios.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ScenariosTab() {
  const { t } = useTranslation()
  const [offset, setOffset] = useState(0)
  const [pending, setPending] = useState<PendingVisibility | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['admin', 'scenarios', offset],
    queryFn: () => admin.listScenarios({ limit: PAGE_SIZE, offset }),
  })

  const scenarios = query.data?.scenarios ?? []
  const total = query.data?.total ?? 0

  return (
    <section className="flex flex-col gap-4" data-testid="scenarios-tab">
      <p className="text-sm text-muted-foreground">{t('admin:scenarios.hint')}</p>
      {notice && <p className="text-sm text-muted-foreground" role="status" data-testid="scenarios-notice">{notice}</p>}
      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="scenarios-error" />}

      {query.isSuccess && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="scenarios-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:scenarios.slug')}</th>
                  <th className={thClass}>{t('admin:scenarios.titleCol')}</th>
                  <th className={thClass}>{t('admin:scenarios.language')}</th>
                  <th className={thClass}>{t('admin:scenarios.visibility')}</th>
                  <th className={thClass}>{t('admin:scenarios.status')}</th>
                  <th className={thClass}>{t('admin:scenarios.version')}</th>
                  <th className={thClass}>{t('admin:scenarios.author')}</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.length === 0 && (
                  <tr><td className={tdClass} colSpan={7} data-testid="scenarios-empty">{t('admin:scenarios.empty')}</td></tr>
                )}
                {scenarios.map(scenario => (
                  <tr key={scenario.id} data-testid="scenario-row" data-scenario-id={scenario.id} data-slug={scenario.slug}>
                    <td className={`${tdClass} text-xs text-muted-foreground`}>{scenario.slug}</td>
                    <td className={tdClass}>{scenario.title}</td>
                    <td className={tdClass}>{scenario.language}</td>
                    <td className={tdClass}>
                      <select
                        className={`${selectClass} w-36`}
                        value={scenario.visibility}
                        aria-label={t('admin:scenarios.visibility')}
                        data-testid="scenario-visibility-select"
                        onChange={event => {
                          const visibility = event.target.value as ScenarioVisibility
                          if (visibility !== scenario.visibility) {
                            setNotice(null)
                            setPending({ scenario, visibility })
                          }
                        }}
                      >
                        {VISIBILITIES.map(value => (
                          <option key={value} value={value}>{t(`admin:scenarios.visibilities.${value}`)}</option>
                        ))}
                      </select>
                    </td>
                    <td className={tdClass}>
                      <Badge variant="secondary" data-testid="scenario-status">
                        {t(`admin:scenarios.statuses.${scenario.status}`, { defaultValue: scenario.status })}
                      </Badge>
                    </td>
                    <td className={tdClass} data-testid="scenario-version">{scenario.current_version_number ?? '—'}</td>
                    <td className={tdClass}>{scenario.author_name ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={total} onOffset={setOffset} testIdPrefix="scenarios" />
        </>
      )}

      {pending && (
        <VisibilityDialog
          key={`${pending.scenario.id}-${pending.visibility}`}
          pending={pending}
          onClose={message => { setPending(null); if (message) setNotice(message) }}
        />
      )}
    </section>
  )
}