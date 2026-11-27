import { Link, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { LogOutIcon, UserIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { LanguageSwitcher } from '@/components/language-switcher'
import { useAuthStore } from '@/stores/auth'

export function AppHeader() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const user = useAuthStore(state => state.user)
  const logout = useAuthStore(state => state.logout)

  const navLink = 'rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground'
  const activeLink = 'bg-accent text-accent-foreground'

  async function handleLogout() {
    await logout()
    await navigate({ to: '/login' })
  }

  return (
    <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4">
        <Link to="/games" className="flex items-center gap-2">
          <span aria-hidden="true" className="font-serif text-3xl leading-none text-gold">✦</span>
          <span className="leading-tight">
            <span className="block font-serif text-lg font-semibold tracking-wide">{t('appTitle')}</span>
            <span className="block text-[10px] uppercase tracking-[0.25em] text-muted-foreground">{t('appTagline')}</span>
          </span>
        </Link>
        <nav className="ml-4 hidden items-center gap-1 sm:flex" aria-label={t('appTitle')}>
          <Link to="/games" className={navLink} activeProps={{ className: activeLink }}>{t('nav.games')}</Link>
          <Link to="/characters" className={navLink} activeProps={{ className: activeLink }}>{t('nav.characters')}</Link>
          <Link to="/author/items" className={navLink} activeProps={{ className: activeLink }} data-testid="nav-author-items">{t('nav.items')}</Link>
          <Link to="/profile" className={navLink} activeProps={{ className: activeLink }}>{t('nav.profile')}</Link>
          {(user?.role === 'admin' || user?.role === 'moderator') && (
            <Link to="/admin" className={navLink} activeProps={{ className: activeLink }} data-testid="nav-admin">{t('admin:navLink')}</Link>
          )}
          <Link to="/demo" className={navLink} activeProps={{ className: activeLink }}>{t('nav.demo')}</Link>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LanguageSwitcher />
          {user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" aria-label={t('user.menu')} data-testid="user-menu">
                  <UserIcon />
                  <span className="max-w-32 truncate">{user.display_name}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{user.email}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleLogout} data-testid="logout">
                  <LogOutIcon />
                  {t('user.logout')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button asChild variant="outline" size="sm">
              <Link to="/login">{t('nav.login')}</Link>
            </Button>
          )}
        </div>
      </div>
      <nav className="flex items-center gap-1 overflow-x-auto border-t px-2 py-1 sm:hidden" aria-label={t('appTitle')}>
        <Link to="/games" className={navLink} activeProps={{ className: activeLink }}>{t('nav.games')}</Link>
        <Link to="/characters" className={navLink} activeProps={{ className: activeLink }}>{t('nav.characters')}</Link>
        <Link to="/author/items" className={navLink} activeProps={{ className: activeLink }} data-testid="nav-author-items-mobile">{t('nav.items')}</Link>
        <Link to="/profile" className={navLink} activeProps={{ className: activeLink }}>{t('nav.profile')}</Link>
        {(user?.role === 'admin' || user?.role === 'moderator') && (
          <Link to="/admin" className={navLink} activeProps={{ className: activeLink }} data-testid="nav-admin-mobile">{t('admin:navLink')}</Link>
        )}
        <Link to="/demo" className={navLink} activeProps={{ className: activeLink }}>{t('nav.demo')}</Link>
      </nav>
    </header>
  )
}