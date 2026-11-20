import { lazy, Suspense } from 'react'
import { createRootRouteWithContext, createRoute, createRouter, Outlet, redirect, useRouterState } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AppHeader } from '@/components/app-header'
import { useAuthStore } from '@/stores/auth'
import { LoginPage } from '@/pages/login-page'
import { ProfilePage } from '@/pages/profile-page'
import { GamesPage } from '@/pages/games-page'
import { GamePage } from '@/pages/game-page'
import { CharactersPage } from '@/pages/characters-page'
import { AdminPage } from '@/pages/admin-page'
import { DemoPage } from '@/pages/demo-page'

interface RouterContext {
  queryClient: QueryClient
}

function RootLayout() {
  // The portal gate ('/') carries its own scene header; the app chrome is
  // hidden there only (task #32A).
  const pathname = useRouterState({ select: state => state.location.pathname })
  return (
    <div className="min-h-screen">
      {pathname !== '/' && <AppHeader />}
      <main>
        <Outlet />
      </main>
    </div>
  )
}

// The portal gate reuses the demo scene and is loaded only on this route.
const PortalPage = lazy(() => import('@/pages/portal-page'))

function PortalRoute() {
  const { t } = useTranslation()
  return (
    <Suspense fallback={<p role="status" className="p-8 text-center text-muted-foreground">{t('loading')}</p>}>
      <PortalPage />
    </Suspense>
  )
}

function NotFound() {
  const { t } = useTranslation()
  return <p className="p-10 text-center font-serif text-2xl text-muted-foreground">{t('notFound')}</p>
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
  notFoundComponent: NotFound,
  beforeLoad: async () => {
    await useAuthStore.getState().ensureLoaded()
  },
})

function requireAuth() {
  if (useAuthStore.getState().status !== 'authenticated') {
    throw redirect({ to: '/' })
  }
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  // Invite survives the anonymous redirect: /games?invite=... -> /?invite=...
  validateSearch: (search: Record<string, unknown>): { invite?: string } => ({
    invite: typeof search.invite === 'string' ? search.invite : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (useAuthStore.getState().status === 'authenticated') {
      throw redirect({ to: '/games', search: search.invite ? { invite: search.invite } : {} })
    }
  },
  component: PortalRoute,
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  // Old sign-in links land on the portal gate, keeping the invite (#31/#32A).
  validateSearch: (search: Record<string, unknown>): { invite?: string } => ({
    invite: typeof search.invite === 'string' ? search.invite : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/', search: search.invite ? { invite: search.invite } : {} })
  },
  component: LoginPage,
})

const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profile',
  beforeLoad: requireAuth,
  component: ProfilePage,
})

const gamesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/games',
  validateSearch: (search: Record<string, unknown>): { invite?: string; characterId?: string } => ({
    invite: typeof search.invite === 'string' ? search.invite : undefined,
    characterId: typeof search.characterId === 'string' ? search.characterId : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (useAuthStore.getState().status !== 'authenticated') {
      throw redirect({ to: '/', search: search.invite ? { invite: search.invite } : {} })
    }
  },
  component: GamesPage,
})

const gameRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/games/$sessionId',
  beforeLoad: requireAuth,
  component: GamePage,
})

const charactersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/characters',
  // Table context for character creation (task #31): scenario pinned by the
  // invite, returnTo is the invite token to auto-seat after creation.
  validateSearch: (search: Record<string, unknown>): { scenarioVersionId?: string; invite?: string } => ({
    scenarioVersionId: typeof search.scenarioVersionId === 'string' ? search.scenarioVersionId : undefined,
    invite: typeof search.invite === 'string' ? search.invite : undefined,
  }),
  beforeLoad: requireAuth,
  component: CharactersPage,
})

const demoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/demo',
  component: DemoPage,
})

// Admin section (task #30): admins see every tab, moderators only the
// supervision tabs (scenarios/complaints, filtered in AdminPage); everyone
// else lands on /games. Backend guards re-check the role on every call.
const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
    tab: typeof search.tab === 'string' ? search.tab : undefined,
  }),
  beforeLoad: () => {
    requireAuth()
    const role = useAuthStore.getState().user?.role
    if (role !== 'admin' && role !== 'moderator') {
      throw redirect({ to: '/games' })
    }
  },
  component: AdminPage,
})

const routeTree = rootRoute.addChildren([indexRoute, loginRoute, profileRoute, gamesRoute, gameRoute, charactersRoute, demoRoute, adminRoute])

export function makeRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: 'intent',
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof makeRouter>
  }
}