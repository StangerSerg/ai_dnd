import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

// Поражение отряда (TPK): финальный рассказ Ведущего и выбор — покинуть стол
// (все) либо начать сначала (только хост). Диалог в стиле kick-dialog.

export function DefeatDialog({ open, finalText, isHost, busy, error, onLeave, onRestart, onDismiss }: {
  open: boolean
  /** Последний Narrated перед маркером PartyDefeated (финальный рассказ). */
  finalText: string
  isHost: boolean
  busy: boolean
  error: string | null
  onLeave: () => void
  onRestart: () => void
  onDismiss: () => void
}) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={next => { if (!next && !busy) onDismiss() }}>
      <DialogContent className="kick-dialog defeat-dialog" data-testid="defeat-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('game:defeat.title')}</DialogTitle>
          <DialogDescription>{t('game:defeat.body')}</DialogDescription>
        </DialogHeader>
        {finalText && (
          <div className="defeat-final" data-testid="defeat-final">
            <span className="defeat-final-label">{t('game:defeat.finalLabel')}</span>
            <p>{finalText}</p>
          </div>
        )}
        {error && <p className="moderation-error" role="alert" data-testid="defeat-error">{error}</p>}
        {!isHost && (
          <p className="defeat-restart-hint" data-testid="defeat-restart-hint">{t('game:defeat.restartHint')}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onLeave} disabled={busy} data-testid="defeat-leave">
            {t('game:defeat.leave')}
          </Button>
          {isHost && (
            <Button onClick={onRestart} disabled={busy} data-testid="defeat-restart">
              {t('game:defeat.restart')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}