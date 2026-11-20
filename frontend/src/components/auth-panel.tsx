import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Ornament } from '@/game/story'
import { ApiError, apiErrorMessage } from '@/api'
import { useAuthStore } from '@/stores/auth'

type Mode = 'login' | 'register'
type FieldErrors = Partial<Record<'email' | 'password' | 'displayName' | 'token', string>>

const emailPattern = /^\S+@\S+\.\S+$/

/**
 * Real authorization inside the demo gate panel (task #32A).
 * Visual shell comes from demo.css (.login-panel); additive styles live in
 * portal.css. On successful login the parent opens the gate doors and
 * navigates to /games — registration/verify stay inside the gate.
 */
export function AuthPanel({ onAuthenticated }: { onAuthenticated: () => void }) {
  const { t } = useTranslation()
  const login = useAuthStore(state => state.login)
  const register = useAuthStore(state => state.register)
  const verifyEmail = useAuthStore(state => state.verifyEmail)

  // Демо-заглушка (#32): в dev-режиме форма без валидации и подтверждения почты —
  // просто создаёт путника в базе и пускает. Боевой режим (prod-сборка) не меняется.
  const demoAuth = import.meta.env.DEV

  const [mode, setMode] = useState<Mode>('login')
  const [pendingEmail, setPendingEmail] = useState<string | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [token, setToken] = useState('')
  const [errors, setErrors] = useState<FieldErrors>({})
  const [apiError, setApiError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function errorText(error: unknown): string {
    if (error instanceof ApiError && error.code) {
      return t(`auth:errors.${error.code}`, { defaultValue: error.detail })
    }
    return apiErrorMessage(error)
  }

  function validate(): boolean {
    if (demoAuth) {
      setErrors({})
      return true
    }
    const next: FieldErrors = {}
    if (!emailPattern.test(email)) next.email = t('auth:validation.email')
    if (mode === 'register' && password.length < 8) next.password = t('auth:validation.password')
    if (mode === 'login' && password.length < 1) next.password = t('auth:validation.passwordRequired')
    if (mode === 'register' && !displayName.trim()) next.displayName = t('auth:validation.displayName')
    setErrors(next)
    return Object.keys(next).length === 0
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setApiError(null)
    setNotice(null)
    if (!validate()) return
    setBusy(true)
    try {
      if (demoAuth) {
        // Поля необязательны: недостающее/некорректное дополняем заглушкой,
        // чтобы запись в базу прошла без валидации и подтверждения.
        const trimmed = email.trim().toLowerCase()
        const demoEmail = emailPattern.test(trimmed)
          ? trimmed
          : `demo-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@demo.local`
        const demoPassword = password.length >= 8 ? password : 'demo-pass-1'
        const demoName = displayName.trim() || 'Путник'
        try {
          await register({ email: demoEmail, password: demoPassword, display_name: demoName })
        } catch (error) {
          if (!(error instanceof ApiError && error.code === 'email_taken')) throw error
        }
        await login({ email: demoEmail, password: demoPassword })
        onAuthenticated()
      } else if (mode === 'login') {
        await login({ email, password })
        // The portal page plays the door-opening animation, then navigates.
        onAuthenticated()
      } else {
        await register({ email, password, display_name: displayName.trim() })
        // Account created but email is not verified yet: no session cookie.
        setPendingEmail(email)
        setToken('')
      }
    } catch (error) {
      if (!demoAuth && error instanceof ApiError && error.code === 'email_not_verified') {
        setPendingEmail(email)
        setToken('')
      } else {
        setApiError(errorText(error))
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setApiError(null)
    if (!token.trim()) {
      setErrors({ token: t('auth:validation.token') })
      return
    }
    setErrors({})
    setBusy(true)
    try {
      await verifyEmail(token.trim())
      setPendingEmail(null)
      setMode('login')
      setNotice(t('auth:verifySuccess'))
    } catch (error) {
      setApiError(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="login-panel" aria-labelledby="portal-auth-title">
      <div className="panel-top"><span>✧</span><span>{t('portal:panelTop')}</span><span>✧</span></div>
      {pendingEmail !== null ? (
        <>
          <h2 id="portal-auth-title">{t('auth:verifyTitle')}</h2>
          <p className="panel-lead">{t('auth:verifyLead', { email: pendingEmail })}</p>
          <Ornament />
          <p className="verify-hint">{t('auth:verifyHint')}</p>
          <form onSubmit={handleVerify} noValidate>
            <label htmlFor="verify-token">{t('auth:verifyToken')}</label>
            <input id="verify-token" value={token} autoComplete="off" aria-invalid={!!errors.token}
              onChange={event => setToken(event.target.value)} data-testid="verify-token" />
            {errors.token && <p className="field-error" role="alert">{errors.token}</p>}
            {apiError && <p className="api-error" role="alert" data-testid="api-error">{apiError}</p>}
            <button className="primary-button" type="submit" disabled={busy} data-testid="verify-submit">
              {busy ? t('portal:submitBusy') : t('auth:verifySubmit')}
            </button>
            <button className="secondary-button auth-back" type="button" data-testid="verify-back"
              onClick={() => { setPendingEmail(null); setMode('login'); setApiError(null); setErrors({}) }}>
              {t('auth:verifyBack')}
            </button>
          </form>
        </>
      ) : (
        <>
          <h2 id="portal-auth-title">{t('auth:title')}</h2>
          <p className="panel-lead">{t('auth:lead')}</p>
          <Ornament />
          {!demoAuth && (
            <div className="auth-tabs" role="tablist" aria-label={t('auth:title')}>
              <button type="button" role="tab" aria-selected={mode === 'login'}
                className={`auth-tab${mode === 'login' ? ' is-active' : ''}`}
                onClick={() => { setMode('login'); setErrors({}); setApiError(null) }}
                data-testid="tab-login">
                {t('auth:loginTab')}
              </button>
              <button type="button" role="tab" aria-selected={mode === 'register'}
                className={`auth-tab${mode === 'register' ? ' is-active' : ''}`}
                onClick={() => { setMode('register'); setErrors({}); setApiError(null) }}
                data-testid="tab-register">
                {t('auth:registerTab')}
              </button>
            </div>
          )}
          {notice && <p className="auth-notice" role="status" data-testid="auth-notice">{notice}</p>}
          <form onSubmit={handleSubmit} noValidate>
            <label htmlFor="email">{t('auth:email')}{demoAuth && <span> {t('portal:optional', { defaultValue: 'необязательно' })}</span>}</label>
            <input id="email" type="email" value={email} autoComplete="email" placeholder="you@example.com"
              aria-invalid={!!errors.email} onChange={event => setEmail(event.target.value)} data-testid="email" />
            {errors.email && <p className="field-error" role="alert">{errors.email}</p>}
            <label htmlFor="password">{t('auth:password')}{demoAuth && <span> {t('portal:optional', { defaultValue: 'необязательно' })}</span>}</label>
            <input id="password" type="password" value={password} placeholder="••••••••"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              aria-invalid={!!errors.password} onChange={event => setPassword(event.target.value)} data-testid="password" />
            {errors.password && <p className="field-error" role="alert">{errors.password}</p>}
            {(mode === 'register' || demoAuth) && (
              <>
                <label htmlFor="display-name">{t('auth:displayName')}{demoAuth && <span> {t('portal:optional', { defaultValue: 'необязательно' })}</span>}</label>
                <input id="display-name" value={displayName} maxLength={80} placeholder={t('portal:namePlaceholder')}
                  autoComplete="nickname" aria-invalid={!!errors.displayName}
                  onChange={event => setDisplayName(event.target.value)} data-testid="display-name" />
                {errors.displayName && <p className="field-error" role="alert">{errors.displayName}</p>}
              </>
            )}
            {apiError && <p className="api-error" role="alert" data-testid="api-error">{apiError}</p>}
            <button className="primary-button" type="submit" disabled={busy} data-testid="submit">
              {busy ? t('portal:submitBusy') : demoAuth || mode === 'login' ? t('portal:submitLogin') : t('portal:submitRegister')} <span>→</span>
            </button>
          </form>
          <p className="privacy-note"><span aria-hidden="true">◇</span> {demoAuth
            ? t('portal:privacyNoteDemo', { defaultValue: 'Демо-вход: путник создаётся в базе сразу, без письма и подтверждения. Поля можно оставить пустыми.' })
            : t('portal:privacyNote')}</p>
        </>
      )}
    </section>
  )
}