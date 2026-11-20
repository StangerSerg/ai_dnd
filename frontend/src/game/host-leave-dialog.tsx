import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

// Выход ведущего из-за стола: передача ведения игроку (transfer-host) либо
// закрытие стола (stop), если игроков нет. Диалог в стиле kick-dialog.

export interface HostLeaveTarget {
  id: string
  name: string
}

/** Локализованный текст ошибки передачи/остановки; неизвестные коды → сетевой. */
export function hostLeaveErrorText(error: unknown, t: TFunction): string {
  if (error instanceof ApiError) {
    const code = error.code ?? error.detail
    return t(`game:hostLeave.errors.${code}`, { defaultValue: t('game:hostLeave.errors.network') })
  }
  return t('game:hostLeave.errors.network')
}

export function HostLeaveDialog({ open, targets, busy, error, onConfirm, onCancel }: {
  open: boolean
  /** Места игроков с user_id, которым можно передать ведение. */
  targets: HostLeaveTarget[]
  busy: boolean
  error: string | null
  /** seatId выбранного игрока → transfer-host; null (игроков нет) → stop. */
  onConfirm: (seatId: string | null) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const hasTargets = targets.length > 0
  const [selected, setSelected] = useState<string | null>(null)

  // При открытии выбираем первого игрока; список мог измениться (кик/выход).
  useEffect(() => {
    if (open) setSelected(targets[0]?.id ?? null)
  }, [open, targets])

  return (
    <Dialog open={open} onOpenChange={next => { if (!next && !busy) onCancel() }}>
      <DialogContent className="kick-dialog host-leave-dialog" data-testid="host-leave-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('game:leaveTableTitle')}</DialogTitle>
          <DialogDescription>
            {hasTargets ? t('game:hostLeave.transferBody') : t('game:hostLeave.closeBody')}
          </DialogDescription>
        </DialogHeader>
        {hasTargets && (
          <fieldset className="host-leave-targets" data-testid="host-leave-targets">
            <legend>{t('game:hostLeave.selectPlayer')}</legend>
            {targets.map(target => (
              <label key={target.id} className="host-leave-target">
                <input
                  type="radio"
                  name="host-leave-target"
                  value={target.id}
                  checked={selected === target.id}
                  disabled={busy}
                  onChange={() => setSelected(target.id)}
                  data-testid="host-leave-target"
                />
                <span className="host-leave-name">{target.name}</span>
                {selected === target.id && (
                  <span className="host-leave-tag">{t('game:hostLeave.newHostTag')}</span>
                )}
              </label>
            ))}
          </fieldset>
        )}
        {error && <p className="moderation-error" role="alert" data-testid="host-leave-error">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy} data-testid="host-leave-cancel">
            {t('game:leaveTableCancel')}
          </Button>
          <Button
            onClick={() => onConfirm(hasTargets ? selected : null)}
            disabled={busy || (hasTargets && !selected)}
            data-testid="host-leave-confirm"
          >
            {hasTargets ? t('game:hostLeave.confirmTransfer') : t('game:hostLeave.confirmClose')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}