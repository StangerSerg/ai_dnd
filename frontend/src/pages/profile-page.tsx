import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api, apiErrorMessage, type Locale } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useSettings } from '@/stores/settings'
import { DiceRenderer, type DiceStyle } from '@/game/dice-renderer'
import '@/demo/demo.css'
import '@/game/game.css'

const DICE_STYLES: readonly DiceStyle[] = ['amber', 'jade', 'amethyst', 'gold', 'malachite', 'ruby']

export function ProfilePage() {
  const { t } = useTranslation()
  const user = useAuthStore(state => state.user)
  const patchMe = useAuthStore(state => state.patchMe)
  const balance = useQuery({ queryKey: ['balance'], queryFn: api.getBalance })
  const diceStyle = useSettings(state => state.diceStyle)
  const setDiceStyle = useSettings(state => state.setDiceStyle)

  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  if (!user) return null

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!displayName.trim()) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await patchMe({ display_name: displayName.trim() })
      setSaved(true)
    } catch (err) {
      setError(apiErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleLocale(locale: Locale) {
    setError(null)
    try {
      await patchMe({ locale })
    } catch (err) {
      setError(apiErrorMessage(err))
    }
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6 px-4 py-10">
      <h1 className="font-serif text-4xl font-medium">{t('profile:title')}</h1>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between gap-3">
            <span className="font-serif text-2xl" data-testid="profile-name">{user.display_name}</span>
            <Badge variant="secondary" data-testid="profile-role">{t(`profile:roles.${user.role}`)}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-muted-foreground">{t('profile:email')}</span>
            <span data-testid="profile-email">{user.email}</span>
          </div>
          <form onSubmit={handleSave} className="flex flex-col gap-3">
            <Label htmlFor="profile-display-name">{t('profile:displayName')}</Label>
            <div className="flex gap-2">
              <Input id="profile-display-name" value={displayName} maxLength={60}
                onChange={event => { setDisplayName(event.target.value); setSaved(false) }} data-testid="profile-display-name" />
              <Button type="submit" disabled={busy || !displayName.trim()} data-testid="profile-save">{t('save')}</Button>
            </div>
            {saved && <p className="text-sm text-muted-foreground" role="status">{t('profile:saved')}</p>}
          </form>
          <div className="flex flex-col gap-3">
            <Label>{t('profile:locale')}</Label>
            <div className="flex gap-2">
              {(['ru', 'en'] as const).map(locale => (
                <Button key={locale} type="button" variant={user.locale === locale ? 'default' : 'outline'}
                  onClick={() => handleLocale(locale)} data-testid={`profile-locale-${locale}`}>
                  {t(`language.${locale}`)}
                </Button>
              ))}
            </div>
          </div>
          {error && <p className="text-sm text-destructive-foreground" role="alert">{error}</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="font-serif text-2xl">{t('profile:diceStyle.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          {/* demo.css palettes are scoped under .codex-demo; the picker carries
              the class (shell reset in game.css) so previews use the real dice. */}
          <div className="codex-demo dice-style-picker" role="radiogroup"
            aria-label={t('profile:diceStyle.title')} data-testid="dice-style-picker">
            {DICE_STYLES.map(style => (
              <button key={style} type="button" role="radio" aria-checked={diceStyle === style}
                className="dice-style-option" onClick={() => setDiceStyle(style)}
                data-testid={`dice-style-${style}`}>
                <DiceRenderer dice={[{ sides: 20, value: 20 }]} eventId={0} appearance={style} kind="2d" />
                <span className="dice-style-name">{t(`profile:diceStyle.styles.${style}`)}</span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="font-serif text-2xl">{t('profile:balance')}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          {balance.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
          {balance.isError && <p role="alert" className="text-destructive-foreground">{apiErrorMessage(balance.error)}</p>}
          {balance.data && (
            <p data-testid="balance">
              <span className="font-serif text-3xl text-gold">{balance.data.credits}</span>{' '}
              <span className="text-muted-foreground">{t('profile:credits')} · ${balance.data.usd}</span>
            </p>
          )}
          <p className="text-muted-foreground">{t('profile:balanceHint')}</p>
        </CardContent>
      </Card>
    </div>
  )
}