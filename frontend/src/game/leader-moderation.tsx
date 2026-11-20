import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'
import { HeartCrackIcon, MicIcon, MicOffIcon, UserXIcon } from 'lucide-react'
import type { TFunction } from 'i18next'
import { ApiError, request } from '@/api/client'
import type { Schemas } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

// Модерация стола лидером команды (#26): API #25 + компактные контролы мест.
type MuteResponse = Schemas['MuteResponse']
type KickResponse = Schemas['KickResponse']

export function muteSeat(sessionId: string, seatId: string, muted: boolean): Promise<MuteResponse> {
  return request<MuteResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/moderation/mute`, {
    method: 'POST',
    body: JSON.stringify({ seat_id: seatId, muted }),
  })
}

export function kickSeat(sessionId: string, seatId: string): Promise<KickResponse> {
  return request<KickResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/moderation/kick`, {
    method: 'POST',
    body: JSON.stringify({ seat_id: seatId }),
  })
}

/** Добровольный выход из-за стола: своё место удаляется без бана, 204 без тела. */
export function leaveSeat(sessionId: string): Promise<void> {
  return request<void>(`/api/sessions/${encodeURIComponent(sessionId)}/seat`, { method: 'DELETE' })
}

/** Локализованный текст ошибки модерации; неизвестные коды → общий сетевой. */
export function moderationErrorText(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    const code = error.code ?? error.detail
    return t(`game:moderation.errors.${code}`, { defaultValue: t('game:moderation.errors.network') })
  }
  return t('game:moderation.errors.network')
}

/** Бейдж мута на месте игрока — виден всем; причина различается (#37). */
export function MutedBadge({ source }: { source?: 'leader' | 'knockout' | null }) {
  const { t } = useTranslation()
  const label = source === 'knockout' ? t('game:moderation.downedBadge')
    : source === 'leader' ? t('game:moderation.leaderBadge')
    : t('game:moderation.mutedBadge')
  return (
    <span
      className={`seat-muted-badge${source === 'knockout' ? ' seat-muted-badge-knockout' : ''}`}
      data-testid="seat-muted-badge"
      data-source={source ?? 'unknown'}
    >
      {source === 'knockout'
        ? <HeartCrackIcon size={10} aria-hidden="true" />
        : <MicOffIcon size={10} aria-hidden="true" />}
      {' '}{label}
    </span>
  )
}

/** Кнопки лидера у места игрока: мут-toggle и кик (danger). */
export function SeatControls({ seatId, muted, busy, unmuteBlocked, onToggleMute, onKick }: {
  seatId: string
  muted: boolean
  busy: boolean
  unmuteBlocked?: boolean
  onToggleMute: () => void
  onKick: () => void
}) {
  const { t } = useTranslation()
  // Нокаут-мут снимается только лечением: лидер не может размьютить упавшего.
  const unmuteForbidden = muted && Boolean(unmuteBlocked)
  const muteLabel = unmuteForbidden
    ? t('game:moderation.unmuteKnockoutTitle')
    : muted ? t('game:moderation.unmute') : t('game:moderation.mute')
  const muteTitle = unmuteForbidden
    ? t('game:moderation.unmuteKnockoutTitle')
    : muted ? t('game:moderation.unmuteTitle') : t('game:moderation.muteTitle')
  return (
    <span className="party-controls">
      <button
        type="button"
        className="moderation-button"
        data-testid={`leader-mute-${seatId}`}
        aria-pressed={muted}
        aria-label={muteLabel}
        title={muteTitle}
        disabled={busy || unmuteForbidden}
        onClick={onToggleMute}
      >
        {muted ? <MicOffIcon size={13} aria-hidden="true" /> : <MicIcon size={13} aria-hidden="true" />}
      </button>
      <button
        type="button"
        className="moderation-button moderation-danger"
        data-testid={`leader-kick-${seatId}`}
        aria-label={t('game:moderation.kick')}
        title={t('game:moderation.kick')}
        disabled={busy}
        onClick={onKick}
      >
        <UserXIcon size={13} aria-hidden="true" />
      </button>
    </span>
  )
}

/** Confirm-диалог кика: имя игрока, предупреждение необратимости. */
export function KickConfirmDialog({ open, name, busy, error, onConfirm, onCancel }: {
  open: boolean
  name: string
  busy: boolean
  error: string | null
  onConfirm: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={next => { if (!next && !busy) onCancel() }}>
      <DialogContent className="kick-dialog" data-testid="kick-confirm-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('game:moderation.kickDialogTitle')}</DialogTitle>
          <DialogDescription>{t('game:moderation.kickDialogBody', { name })}</DialogDescription>
        </DialogHeader>
        {error && <p className="moderation-error" role="alert">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy} data-testid="kick-confirm-no">
            {t('game:moderation.kickCancel')}
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={busy} data-testid="kick-confirm-yes">
            {t('game:moderation.kickConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Баннер удалённому игроку: лента читаема, действия заблокированы, выход в зал. */
export function KickedBanner() {
  const { t } = useTranslation()
  return (
    <section className="kicked-banner" role="alert" data-testid="kicked-banner">
      <div>
        <strong>{t('game:moderation.kickedBanner')}</strong>
        <p>{t('game:moderation.kickedHint')}</p>
      </div>
      <Link to="/games" className="primary-button kicked-to-hall" data-testid="kicked-to-hall">
        {t('game:moderation.kickedToHall')}
      </Link>
    </section>
  )
}