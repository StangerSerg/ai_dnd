import { useEffect, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { GateAmbience } from '@/demo/GateAmbience'
import { AuthPanel } from '@/components/auth-panel'
import { setLocale, isLocale } from '@/i18n'
import '@/demo/demo.css'
import '@/portal.css'

/**
 * Real landing page (task #32A): the demo gate scene (castle, torches, moon)
 * with a working authorization panel. Successful login opens the gate doors
 * (demo animation) and then navigates to /games, preserving the invite token.
 */
export default function PortalPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  // Invite survives anonymous redirects: /games?invite=... -> /?invite=...
  const { invite } = useSearch({ from: '/' })
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    if (!opening) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timer = window.setTimeout(() => {
      void navigate({ to: '/games', search: invite ? { invite } : {} })
    }, reduced ? 0 : 850)
    return () => window.clearTimeout(timer)
  }, [opening, invite, navigate])

  const locale = isLocale(i18n.language) ? i18n.language : 'ru'

  return (
    <div className={`app codex-demo portal-page screen-${opening ? 'opening' : 'gate'}`}>
      <main className="gate scene">
        <GateAmbience />
        <div className="gate-doors" aria-hidden="true"><div /><div /></div>
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">✦</span>
            <span>{t('portal:brand')} <b>{t('portal:brandBold')}</b><small>{t('portal:brandSmall')}</small></span>
          </div>
          <div className="header-actions">
            <span className="header-note"><span className="status-dot" /> {t('portal:headerNote')}</span>
            <div className="lang-switch" role="group" aria-label={t('language.label')}>
              <button type="button" className={`text-button${locale === 'ru' ? ' is-active' : ''}`}
                aria-pressed={locale === 'ru'} onClick={() => setLocale('ru')}>RU</button>
              <button type="button" className={`text-button${locale === 'en' ? ' is-active' : ''}`}
                aria-pressed={locale === 'en'} onClick={() => setLocale('en')}>EN</button>
            </div>
          </div>
        </header>
        <div className="gate-content" inert={opening}>
          <div className="gate-intro">
            <div className="eyebrow"><span /> {t('portal:eyebrow')}</div>
            <h1 tabIndex={-1}>{t('portal:titleLine1')}<br /><em>{t('portal:titleLine2')}</em></h1>
            <p>{t('portal:introLine1')}<br className="desktop-break" /> {t('portal:introLine2')}</p>
          </div>
          <AuthPanel onAuthenticated={() => setOpening(true)} />
          <div className="intro-signature"><span>✧</span> {t('portal:signature')}</div>
        </div>
        <footer className="scene-footer">
          <span>✦ &nbsp; {t('portal:footerLeft')}</span>
          <span>{t('portal:footerRight')} &nbsp; ✦</span>
        </footer>
        {opening && <p className="opening-status" role="status">{t('portal:opening')}</p>}
      </main>
    </div>
  )
}