// /admin page shell (task #30): native role=tab buttons + lazy tab bodies.
// Wave 2 (supervision #28 / ops #29) appends entries to ADMIN_TABS; each tab
// declares the roles allowed to see it (mirrors the backend guards).
import type { ComponentType } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth'
import { UsersTab } from '@/components/admin/users-tab'
import { BillingTab } from '@/components/admin/billing-tab'
import { AuditTab } from '@/components/admin/audit-tab'
import { SessionsTab } from '@/components/admin/sessions-tab'
import { ScenariosTab } from '@/components/admin/scenarios-tab'
import { ComplaintsTab } from '@/components/admin/complaints-tab'
import { OpsTab } from '@/components/admin/ops-tab'

export interface AdminTabDef {
  id: string
  labelKey: string
  Component: ComponentType
  /** Roles allowed to see the tab; backend guards re-check on every call. */
  roles: string[]
}

// Registration order = tab order. Only the active tab mounts, so its queries
// load lazily on first activation (and refetch per TanStack Query defaults).
// Moderators only get scenarios/complaints (supervision.py ModDep); the first
// available tab for them is therefore «scenarios».
export const ADMIN_TABS: AdminTabDef[] = [
  { id: 'users', labelKey: 'admin:tabs.users', Component: UsersTab, roles: ['admin'] },
  { id: 'billing', labelKey: 'admin:tabs.billing', Component: BillingTab, roles: ['admin'] },
  { id: 'audit', labelKey: 'admin:tabs.audit', Component: AuditTab, roles: ['admin'] },
  { id: 'sessions', labelKey: 'admin:tabs.sessions', Component: SessionsTab, roles: ['admin'] },
  { id: 'scenarios', labelKey: 'admin:tabs.scenarios', Component: ScenariosTab, roles: ['admin', 'moderator'] },
  { id: 'complaints', labelKey: 'admin:tabs.complaints', Component: ComplaintsTab, roles: ['admin', 'moderator'] },
  { id: 'ops', labelKey: 'admin:tabs.ops', Component: OpsTab, roles: ['admin'] },
]

export function AdminPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { tab } = useSearch({ from: '/admin' })
  const role = useAuthStore(state => state.user?.role)
  const visibleTabs = ADMIN_TABS.filter(item => role !== undefined && item.roles.includes(role))
  // A ?tab= that the role cannot access falls back to the first available tab
  // (no redirect); the router guard keeps other roles out of /admin entirely.
  const active = visibleTabs.find(item => item.id === tab) ?? visibleTabs[0]

  if (!active) return null
  const ActiveComponent = active.Component

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10" data-testid="admin-page">
      <div className="flex flex-col gap-1">
        <h1 className="font-serif text-4xl font-medium">{t('admin:title')}</h1>
        <p className="text-sm text-muted-foreground">{t('admin:hint')}</p>
      </div>

      <div role="tablist" aria-label={t('admin:title')} className="flex flex-wrap gap-1 border-b pb-px">
        {visibleTabs.map(item => {
          const selected = item.id === active.id
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={selected}
              data-testid={`admin-tab-${item.id}`}
              className={`-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm transition-colors ${
                selected
                  ? 'border-primary font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
              onClick={() => void navigate({ to: '/admin', search: { tab: item.id } })}
            >
              {t(item.labelKey)}
            </button>
          )
        })}
      </div>

      <div role="tabpanel" data-testid={`admin-panel-${active.id}`}>
        <ActiveComponent />
      </div>
    </div>
  )
}