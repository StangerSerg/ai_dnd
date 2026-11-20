// Admin «Пользователи» tab (task #30, wave 1): GET /api/admin/users with
// search/pagination, role change / ban / reset-sessions behind confirm dialogs.
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { admin, type AdminRole, type AdminUser } from '@/api'
import { Pager, QueryError, formatDateTime, selectClass, tableClass, tdClass, thClass, useAdminErrorText } from './common'

const PAGE_SIZE = 20
const ROLES: AdminRole[] = ['user', 'moderator', 'admin']

type ActionKind = 'role' | 'ban' | 'reset'

interface PendingAction {
  kind: ActionKind
  user: AdminUser
}

function UserActionDialog({ action, onClose }: { action: PendingAction; onClose: (message?: string) => void }) {
  const { t } = useTranslation()
  const errorText = useAdminErrorText()
  const queryClient = useQueryClient()
  // AdminUserItem.role is a plain string in the contract; the picker only
  // offers the known RoleUpdateRequest values.
  const [role, setRole] = useState<AdminRole>(action.user.role as AdminRole)
  const [error, setError] = useState<string | null>(null)
  const { kind, user } = action
  const isUnban = kind === 'ban' && user.banned_at != null

  const mutation = useMutation({
    mutationFn: async () => {
      if (kind === 'role') return admin.changeRole(user.id, role)
      if (kind === 'ban') return admin.setBan(user.id, user.banned_at == null)
      return admin.resetSessions(user.id)
    },
    onSuccess: data => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
      if (kind === 'reset' && 'revoked' in data) {
        onClose(t('admin:users.resetDone', { revoked: data.revoked }))
      } else {
        onClose()
      }
    },
    onError: err => setError(errorText(err)),
  })

  const titleKey =
    kind === 'role' ? 'admin:users.roleDialogTitle'
      : kind === 'reset' ? 'admin:users.resetDialogTitle'
        : isUnban ? 'admin:users.unbanDialogTitle' : 'admin:users.banDialogTitle'
  const hintKey =
    kind === 'role' ? 'admin:users.roleDialogHint'
      : kind === 'reset' ? 'admin:users.resetDialogHint'
        : isUnban ? 'admin:users.unbanDialogHint' : 'admin:users.banDialogHint'

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent data-testid={`${kind}-dialog`}>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t(titleKey)}</DialogTitle>
          <DialogDescription>{t(hintKey, { email: user.email ?? user.display_name })}</DialogDescription>
        </DialogHeader>
        {kind === 'role' && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="admin-role-select">{t('admin:users.newRole')}</Label>
            <select id="admin-role-select" className={selectClass} value={role}
              onChange={event => setRole(event.target.value as AdminRole)} data-testid="role-select">
              {ROLES.map(value => (
                <option key={value} value={value}>{t(`admin:roles.${value}`)}</option>
              ))}
            </select>
          </div>
        )}
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="user-action-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onClose()} data-testid={`${kind}-cancel`}>{t('cancel')}</Button>
          <Button type="button" variant={kind === 'role' ? 'default' : 'destructive'}
            disabled={mutation.isPending || (kind === 'role' && role === user.role)}
            onClick={() => mutation.mutate()} data-testid={`${kind}-confirm`}>
            {t('admin:users.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function UsersTab() {
  const { t, i18n } = useTranslation()
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['admin', 'users', q, offset],
    queryFn: () => admin.listUsers({ q: q || undefined, limit: PAGE_SIZE, offset }),
  })

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setQ(searchInput.trim())
    setOffset(0)
  }

  const users = query.data?.users ?? []
  const total = query.data?.total ?? 0

  return (
    <section className="flex flex-col gap-4" data-testid="users-tab">
      <form onSubmit={handleSearch} className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-64 flex-col gap-2">
          <Label htmlFor="admin-users-search">{t('admin:users.search')}</Label>
          <Input id="admin-users-search" value={searchInput} maxLength={255}
            placeholder={t('admin:users.searchPlaceholder')}
            onChange={event => setSearchInput(event.target.value)} data-testid="users-search" />
        </div>
        <Button type="submit" variant="outline" data-testid="users-search-submit">{t('admin:users.searchSubmit')}</Button>
      </form>

      {notice && <p className="text-sm text-muted-foreground" role="status" data-testid="users-notice">{notice}</p>}
      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && <QueryError error={query.error} onRetry={() => query.refetch()} testId="users-error" />}

      {query.isSuccess && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className={tableClass} data-testid="users-table">
              <thead>
                <tr>
                  <th className={thClass}>{t('admin:users.email')}</th>
                  <th className={thClass}>{t('admin:users.name')}</th>
                  <th className={thClass}>{t('admin:users.role')}</th>
                  <th className={thClass}>{t('admin:users.credits')}</th>
                  <th className={thClass}>{t('admin:users.status')}</th>
                  <th className={thClass}>{t('admin:users.created')}</th>
                  <th className={thClass}>{t('admin:users.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {users.length === 0 && (
                  <tr><td className={tdClass} colSpan={7} data-testid="users-empty">{t('admin:users.empty')}</td></tr>
                )}
                {users.map(user => (
                  <tr key={user.id} data-testid="user-row" data-user-id={user.id} data-email={user.email ?? ''}>
                    <td className={tdClass}>{user.email ?? '—'}</td>
                    <td className={tdClass}>{user.display_name}</td>
                    <td className={tdClass}>
                      <Badge variant={user.role === 'admin' ? 'default' : 'secondary'} data-testid="user-role">
                        {t(`admin:roles.${user.role}`, { defaultValue: user.role })}
                      </Badge>
                    </td>
                    <td className={tdClass} data-testid="user-credits">{user.credits}</td>
                    <td className={tdClass}>
                      {user.banned_at != null
                        ? <Badge variant="destructive" data-testid="user-banned">{t('admin:users.banned')}</Badge>
                        : <Badge variant="outline" data-testid="user-active">{t('admin:users.active')}</Badge>}
                    </td>
                    <td className={`${tdClass} whitespace-nowrap`}>{formatDateTime(user.created_at, i18n.language)}</td>
                    <td className={tdClass}>
                      <div className="flex flex-wrap gap-1">
                        <Button variant="outline" size="sm" data-testid="user-role-open"
                          onClick={() => { setNotice(null); setPending({ kind: 'role', user }) }}>
                          {t('admin:users.roleAction')}
                        </Button>
                        <Button variant="outline" size="sm" data-testid="user-ban-open"
                          onClick={() => { setNotice(null); setPending({ kind: 'ban', user }) }}>
                          {user.banned_at != null ? t('admin:users.unbanAction') : t('admin:users.banAction')}
                        </Button>
                        <Button variant="outline" size="sm" data-testid="user-reset-open"
                          onClick={() => { setNotice(null); setPending({ kind: 'reset', user }) }}>
                          {t('admin:users.resetAction')}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={total} onOffset={setOffset} testIdPrefix="users" />
        </>
      )}

      {pending && (
        <UserActionDialog
          key={`${pending.kind}-${pending.user.id}`}
          action={pending}
          onClose={message => { setPending(null); if (message) setNotice(message) }}
        />
      )}
    </section>
  )
}