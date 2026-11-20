import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'

// The Codex demo (dice included) is preserved as-is and loaded only on this route.
const DemoApp = lazy(() => import('@/demo/DemoApp'))

export function DemoPage() {
  const { t } = useTranslation()
  return (
    <div>
      <p className="border-b border-gold/40 bg-secondary px-4 py-2 text-center text-xs text-secondary-foreground" data-testid="demo-note">
        {t('demo:note')}
      </p>
      <Suspense fallback={<p role="status" className="p-8 text-center text-muted-foreground">{t('loading')}</p>}>
        <DemoApp />
      </Suspense>
    </div>
  )
}