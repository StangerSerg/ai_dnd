// Shared building blocks for the admin section (task #30). Native elements +
// tailwind, following the games-page.tsx pattern (no shadcn table/tabs/select).
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { ApiError, apiErrorMessage } from '@/api'

export const selectClass =
  'h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none'

export const tableClass = 'w-full border-collapse text-sm'
export const thClass = 'border-b px-3 py-2 text-left font-medium text-muted-foreground whitespace-nowrap'
export const tdClass = 'border-b px-3 py-2 align-top'

/** Maps backend machine codes to admin:errors.* with the raw detail as fallback. */
export function useAdminErrorText() {
  const { t } = useTranslation()
  return (error: unknown): string => {
    if (error instanceof ApiError && error.code) {
      return t(`admin:errors.${error.code}`, { defaultValue: error.detail })
    }
    return apiErrorMessage(error)
  }
}

export function formatDateTime(iso: string, language: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(language === 'ru' ? 'ru-RU' : 'en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function QueryError({ error, onRetry, testId }: { error: unknown; onRetry: () => void; testId?: string }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  return (
    <div role="alert" className="flex items-center gap-3 text-destructive-foreground" data-testid={testId}>
      <span>{errorText(error)}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>{t('retry')}</Button>
    </div>
  )
}

export function Pager({
  offset,
  limit,
  total,
  onOffset,
  testIdPrefix,
}: {
  offset: number
  limit: number
  total: number
  onOffset: (offset: number) => void
  testIdPrefix: string
}) {
  const { t } = useTranslation()
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + limit, total)
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span data-testid={`${testIdPrefix}-total`}>{t('admin:pager.showing', { from, to, total })}</span>
      <Button variant="outline" size="sm" disabled={offset <= 0}
        onClick={() => onOffset(Math.max(0, offset - limit))} data-testid={`${testIdPrefix}-prev`}>
        {t('admin:pager.prev')}
      </Button>
      <Button variant="outline" size="sm" disabled={offset + limit >= total}
        onClick={() => onOffset(offset + limit)} data-testid={`${testIdPrefix}-next`}>
        {t('admin:pager.next')}
      </Button>
    </div>
  )
}

/** Collapsible pretty-printed JSON block (wave 2: session setup/state, event payloads). */
export function JsonBlock({ label, value, testId, defaultOpen = false }: {
  label: string
  value: unknown
  testId: string
  defaultOpen?: boolean
}) {
  return (
    <details className="rounded-md border" open={defaultOpen} data-testid={testId}>
      <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-muted-foreground">
        {label}
      </summary>
      <pre className="max-h-72 overflow-auto border-t px-3 py-2 text-xs leading-relaxed" data-testid={`${testId}-pre`}>
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  )
}