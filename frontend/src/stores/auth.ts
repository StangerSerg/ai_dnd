import { create } from 'zustand'
import { api, ApiError, type LoginRequest, type MePatch, type RegisterRequest, type User } from '@/api'
import { applyUserLocale, isLocale, setLocale } from '@/i18n'
import { useSettings } from '@/stores/settings'

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'anonymous'

interface AuthState {
  user: User | null
  status: AuthStatus
  ensureLoaded: () => Promise<void>
  login: (body: LoginRequest) => Promise<User>
  /** Register creates an unverified account; login is possible after verify-email. */
  register: (body: RegisterRequest) => Promise<User>
  verifyEmail: (token: string) => Promise<void>
  logout: () => Promise<void>
  patchMe: (patch: MePatch) => Promise<User>
}

let loading: Promise<void> | null = null

function applyLocale(user: User) {
  if (isLocale(user.locale)) applyUserLocale(user.locale)
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  status: 'idle',

  // Bootstrap through GET /api/me (httpOnly cookie session).
  ensureLoaded: () => {
    if (get().status !== 'idle') return Promise.resolve()
    loading ??= (async () => {
      set({ status: 'loading' })
      try {
        const user = await api.getMe()
        applyLocale(user)
        useSettings.getState().hydrateFromServer(user.ui_settings)
        set({ user, status: 'authenticated' })
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) {
          console.warn('auth bootstrap failed', error)
        }
        set({ user: null, status: 'anonymous' })
      } finally {
        loading = null
      }
    })()
    return loading
  },

  login: async body => {
    const user = await api.login(body)
    applyLocale(user)
    useSettings.getState().hydrateFromServer(user.ui_settings)
    set({ user, status: 'authenticated' })
    return user
  },

  register: async body => {
    // 201 + MeResponse with email_verified=false; no session cookie yet.
    return api.register(body)
  },

  verifyEmail: async token => {
    await api.verifyEmail({ token })
  },

  logout: async () => {
    await api.logout()
    set({ user: null, status: 'anonymous' })
  },

  patchMe: async patch => {
    const user = await api.patchMe(patch)
    if (patch.locale) setLocale(patch.locale)
    set({ user, status: 'authenticated' })
    return user
  },
}))