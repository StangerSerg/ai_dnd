import { useTranslation } from 'react-i18next'
import type { HallTable } from './hall-table'

const MIN_SEATS = 1
const MAX_SEATS = 12
const DEFAULT_SEATS = 4

/** Leader-only control: open/close recruiting and set table capacity (live). */
export function HallRecruitingControl({
  table,
  pending,
  onApply,
}: {
  table: HallTable
  pending: boolean
  onApply: (next: { recruiting: boolean; seats_total: number | null }) => void
}) {
  const { t } = useTranslation()
  const unlimited = table.seatsTotal == null
  const seats = table.seatsTotal ?? DEFAULT_SEATS
  const free =
    table.seatsTotal != null && table.seatsTaken != null
      ? Math.max(0, table.seatsTotal - table.seatsTaken)
      : null

  function toggleRecruiting() {
    onApply({ recruiting: !table.recruiting, seats_total: table.seatsTotal })
  }
  function setSeats(value: number) {
    const clamped = Math.min(MAX_SEATS, Math.max(MIN_SEATS, value))
    onApply({ recruiting: table.recruiting, seats_total: clamped })
  }
  function toggleUnlimited() {
    onApply({
      recruiting: table.recruiting,
      seats_total: unlimited ? Math.max(MIN_SEATS, Math.min(MAX_SEATS, seats)) : null,
    })
  }

  return (
    <section className="hall-recruiting" aria-label={t('games:hall.recruit.title')} data-testid="hall-recruiting">
      <div className="hall-recruiting-head">
        <span className="hall-recruiting-kicker">{t('games:hall.recruit.title')}</span>
        <span className={`hall-recruiting-state ${table.recruiting ? 'is-open' : 'is-closed'}`} data-testid="recruiting-state">
          {table.recruiting ? t('games:hall.recruit.open') : t('games:hall.recruit.closed')}
        </span>
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={table.recruiting}
        className={`hall-switch ${table.recruiting ? 'is-on' : ''}`}
        onClick={toggleRecruiting}
        disabled={pending}
        data-testid="recruiting-toggle"
      >
        <span className="hall-switch-track" aria-hidden="true"><span className="hall-switch-thumb" /></span>
        <span className="hall-switch-label">{t('games:hall.recruit.toggle')}</span>
      </button>

      <div className="hall-capacity">
        <span className="hall-capacity-label" id={`hall-capacity-label-${table.id}`}>
          {t('games:hall.recruit.seats')}
        </span>
        <div className="hall-stepper" role="group" aria-labelledby={`hall-capacity-label-${table.id}`}>
          <button
            type="button"
            aria-label={t('games:hall.recruit.decrease')}
            disabled={pending || unlimited || seats <= MIN_SEATS}
            onClick={() => setSeats(seats - 1)}
            data-testid="seats-decrease"
          >−</button>
          <output aria-live="polite" data-testid="seats-value">{unlimited ? '∞' : seats}</output>
          <button
            type="button"
            aria-label={t('games:hall.recruit.increase')}
            disabled={pending || unlimited || seats >= MAX_SEATS}
            onClick={() => setSeats(seats + 1)}
            data-testid="seats-increase"
          >+</button>
        </div>
        <button
          type="button"
          className={`hall-unlimited ${unlimited ? 'is-on' : ''}`}
          aria-pressed={unlimited}
          onClick={toggleUnlimited}
          disabled={pending}
          data-testid="seats-unlimited"
        >
          {t('games:hall.recruit.unlimited')}
        </button>
      </div>

      <p className="hall-recruiting-status" data-testid="recruiting-status">
        {table.recruiting
          ? free != null
            ? t('games:hall.recruit.statusOpenFree', { free })
            : t('games:hall.recruit.statusOpen')
          : t('games:hall.recruit.statusClosed')}
        {table.recruiting && !table.open && (
          <span className="hall-recruiting-note"> · {t('games:hall.recruit.notVisible')}</span>
        )}
      </p>
    </section>
  )
}