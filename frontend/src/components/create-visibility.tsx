import { useTranslation } from 'react-i18next'

// Блок «Публичный стол + ёмкость» для диалога создания стола. Переиспользует
// разметку лидерского контроля набора (классы hall-switch / hall-capacity /
// hall-stepper / hall-unlimited), чтобы публичность задавалась прямо при
// создании и не требовала лишних шагов в зале. Сам hall-recruiting.tsx при этом
// НЕ трогаем: диалог через Radix Portal рендерится в <body> вне .codex-demo.hall-page,
// поэтому стили продублированы в hall.css под собственным скоупом .hall-create-dialog.

const MIN_SEATS = 1
const MAX_SEATS = 12
const DEFAULT_SEATS = 4

export interface CreateVisibilityValue {
  /** Публичный стол: виден в зале историй, к нему могут присоединяться. */
  recruiting: boolean
  /** Ёмкость стола 1..12; null = без ограничения. */
  seats_total: number | null
}

/** Начальное значение: приватный стол ёмкостью 4 (по решению владельца). */
export const defaultCreateVisibility: CreateVisibilityValue = {
  recruiting: false,
  seats_total: DEFAULT_SEATS,
}

export function CreateVisibilityControl({
  value,
  onChange,
  disabled = false,
}: {
  value: CreateVisibilityValue
  onChange: (next: CreateVisibilityValue) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const isPublic = value.recruiting
  const unlimited = value.seats_total == null
  const seats = value.seats_total ?? DEFAULT_SEATS

  function togglePublic() {
    onChange({ ...value, recruiting: !isPublic })
  }
  function setSeats(next: number) {
    onChange({ ...value, seats_total: Math.min(MAX_SEATS, Math.max(MIN_SEATS, next)) })
  }
  function toggleUnlimited() {
    onChange({ ...value, seats_total: unlimited ? Math.min(MAX_SEATS, Math.max(MIN_SEATS, seats)) : null })
  }

  return (
    <section
      className="hall-recruiting hall-create-visibility"
      aria-label={t('games:visibility.title')}
      data-testid="create-visibility"
    >
      <div className="hall-recruiting-head">
        <span className="hall-recruiting-kicker">{t('games:visibility.title')}</span>
        <span
          className={`hall-recruiting-state ${isPublic ? 'is-open' : 'is-closed'}`}
          data-testid="create-visibility-state"
        >
          {isPublic ? t('games:visibility.public') : t('games:visibility.private')}
        </span>
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={isPublic}
        className={`hall-switch ${isPublic ? 'is-on' : ''}`}
        onClick={togglePublic}
        disabled={disabled}
        data-testid="create-public-toggle"
      >
        <span className="hall-switch-track" aria-hidden="true"><span className="hall-switch-thumb" /></span>
        <span className="hall-switch-label">{t('games:visibility.toggle')}</span>
      </button>

      {isPublic && (
        <div className="hall-capacity">
          <span className="hall-capacity-label" id="create-visibility-capacity-label">
            {t('games:visibility.seats')}
          </span>
          <div className="hall-stepper" role="group" aria-labelledby="create-visibility-capacity-label">
            <button
              type="button"
              aria-label={t('games:hall.recruit.decrease')}
              disabled={disabled || unlimited || seats <= MIN_SEATS}
              onClick={() => setSeats(seats - 1)}
              data-testid="create-seats-decrease"
            >−</button>
            <output aria-live="polite" data-testid="create-seats-value">{unlimited ? '∞' : seats}</output>
            <button
              type="button"
              aria-label={t('games:hall.recruit.increase')}
              disabled={disabled || unlimited || seats >= MAX_SEATS}
              onClick={() => setSeats(seats + 1)}
              data-testid="create-seats-increase"
            >+</button>
          </div>
          <button
            type="button"
            className={`hall-unlimited ${unlimited ? 'is-on' : ''}`}
            aria-pressed={unlimited}
            onClick={toggleUnlimited}
            disabled={disabled}
            data-testid="create-seats-unlimited"
          >
            {t('games:hall.recruit.unlimited')}
          </button>
        </div>
      )}

      <p className="hall-recruiting-status" data-testid="create-visibility-status">
        {isPublic ? t('games:visibility.publicHint') : t('games:visibility.privateHint')}
      </p>
    </section>
  )
}
